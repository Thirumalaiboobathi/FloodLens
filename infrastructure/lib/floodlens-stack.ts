import * as fs from "node:fs";
import * as path from "node:path";
import * as cdk from "aws-cdk-lib";
import * as apigwv2 from "aws-cdk-lib/aws-apigatewayv2";
import { HttpLambdaIntegration } from "aws-cdk-lib/aws-apigatewayv2-integrations";
import * as cloudfront from "aws-cdk-lib/aws-cloudfront";
import * as origins from "aws-cdk-lib/aws-cloudfront-origins";
import * as cr from "aws-cdk-lib/custom-resources";
import * as iam from "aws-cdk-lib/aws-iam";
import * as cloudwatch from "aws-cdk-lib/aws-cloudwatch";
import * as cwActions from "aws-cdk-lib/aws-cloudwatch-actions";
import * as lambda from "aws-cdk-lib/aws-lambda";
import * as location from "aws-cdk-lib/aws-location";
import * as logs from "aws-cdk-lib/aws-logs";
import * as s3 from "aws-cdk-lib/aws-s3";
import * as s3deploy from "aws-cdk-lib/aws-s3-deployment";
import * as sns from "aws-cdk-lib/aws-sns";
import * as subs from "aws-cdk-lib/aws-sns-subscriptions";
import type { Construct } from "constructs";

export const REGION = "ap-south-1";

export interface FloodLensProps extends cdk.StackProps {
  /** Built React app (apps/web/dist). */
  webDistDir: string;
  /** Output of scripts/package_lambda.py (infrastructure/build/lambda). */
  lambdaDir: string;
  /** Optional: receives CloudWatch alarm notifications. Omit to create alarms without a subscriber. */
  alarmEmail?: string;
  /** Optional ISO-8601 expiry for the browser map key. Omit for no expiry (rotate manually). */
  locationKeyExpireTime?: string;
  /** Amazon Location style served to the browser: Standard | Monochrome | Hybrid | Satellite. */
  mapStyle?: string;
  /** Extra origins allowed to use the map key (for example a custom domain). */
  extraReferers?: string[];
  /** HTTP API steady-state limit in requests/second (default 50). Together with the burst limit this bounds API spend. */
  apiRateLimit?: number;
  /** HTTP API burst limit (default 100). */
  apiBurstLimit?: number;
  /**
   * Optional Lambda reserved concurrency. Unset by default: new accounts can have a low concurrency quota
   * and a reservation that leaves under 100 unreserved executions makes the deployment fail.
   */
  reservedConcurrency?: number;
}

/**
 * Query parameters the API reads (every query parameter in the OpenAPI document; a Python test keeps
 * this list in step). The CloudFront cache policy forwards and keys on exactly these, so unknown
 * parameters cannot be used to defeat the cache.
 */
export const API_QUERY_PARAMS = [
  "bbox", "boundary", "end", "facility_type", "feature_type", "ids", "kind", "limit", "min_class",
  "offset", "scenario", "start", "stream", "water_class", "zoom",
];

function requireDir(dir: string, hint: string): string {
  const abs = path.resolve(dir);
  if (!fs.existsSync(abs) || !fs.statSync(abs).isDirectory()) {
    throw new Error(`${abs} does not exist. ${hint}`);
  }
  return abs;
}

/**
 * FloodLens: static React app (private S3 + CloudFront OAC), read-only FastAPI on Lambda behind an
 * HTTP API, Amazon Location map key, CloudWatch alarms. No geospatial processing runs here: the API
 * serves artifacts built offline and shipped inside the Lambda package.
 */
export class FloodLensStack extends cdk.Stack {
  constructor(scope: Construct, id: string, props: FloodLensProps) {
    super(scope, id, { ...props, env: { account: props.env?.account, region: REGION } });

    const webDist = requireDir(props.webDistDir, "Build it first: cd apps/web && npm run build");
    const lambdaDir = requireDir(props.lambdaDir, "Build it first: python scripts/package_lambda.py");
    const mapStyle = props.mapStyle ?? "Monochrome";

    // ---- static site bucket: private, TLS only, never public --------------------------------------
    const siteBucket = new s3.Bucket(this, "SiteBucket", {
      blockPublicAccess: s3.BlockPublicAccess.BLOCK_ALL,
      encryption: s3.BucketEncryption.S3_MANAGED,
      enforceSSL: true,
      objectOwnership: s3.ObjectOwnership.BUCKET_OWNER_ENFORCED,
      versioned: true,
      removalPolicy: cdk.RemovalPolicy.RETAIN, // never deleted by a stack update or destroy
      lifecycleRules: [{ noncurrentVersionExpiration: cdk.Duration.days(30) }],
    });

    // ---- API: Lambda (FastAPI + Mangum) behind an HTTP API ------------------------------------------
    const fnLogs = new logs.LogGroup(this, "ApiFunctionLogs", {
      retention: logs.RetentionDays.ONE_MONTH, removalPolicy: cdk.RemovalPolicy.RETAIN,
    });
    const apiFn = new lambda.Function(this, "ApiFunction", {
      description: "FloodLens read-only API (FastAPI via Mangum); serves prebuilt artifacts",
      runtime: lambda.Runtime.PYTHON_3_13,
      architecture: lambda.Architecture.ARM_64,
      handler: "floodlens_api.lambda_handler.handler",
      code: lambda.Code.fromAsset(lambdaDir),
      memorySize: 1024, // artifact JSON is cached in memory; also scales CPU
      timeout: cdk.Duration.seconds(15),
      logGroup: fnLogs,
      environment: { FLOODLENS_ARTIFACT_DIR: "/var/task/data/api" },
      reservedConcurrentExecutions: props.reservedConcurrency,
    });
    // The execution role is only the managed logging permissions CDK adds for logGroup: the function
    // reads files in its own package and calls no other AWS service.

    const accessLogs = new logs.LogGroup(this, "ApiAccessLogs", {
      retention: logs.RetentionDays.ONE_MONTH, removalPolicy: cdk.RemovalPolicy.RETAIN,
    });
    const httpApi = new apigwv2.HttpApi(this, "HttpApi", {
      apiName: "floodlens-api",
      description: "FloodLens read-only exposure and rainfall-context API",
    });
    httpApi.addRoutes({
      path: "/v1/{proxy+}",
      methods: [apigwv2.HttpMethod.GET, apigwv2.HttpMethod.HEAD],
      integration: new HttpLambdaIntegration("ApiIntegration", apiFn),
    });
    const stage = httpApi.defaultStage!.node.defaultChild as apigwv2.CfnStage;
    stage.defaultRouteSettings = {
      throttlingRateLimit: props.apiRateLimit ?? 50, throttlingBurstLimit: props.apiBurstLimit ?? 100,
    };
    stage.accessLogSettings = {
      destinationArn: accessLogs.logGroupArn,
      format: JSON.stringify({
        requestId: "$context.requestId", ip: "$context.identity.sourceIp", method: "$context.httpMethod",
        path: "$context.path", status: "$context.status", latencyMs: "$context.responseLatency",
        integrationError: "$context.integrationErrorMessage", bytes: "$context.responseLength",
      }),
    };

    // ---- CloudFront: one origin for the app and the API (same-origin, no CORS) -------------------------
    const spaRewrite = new cloudfront.Function(this, "SpaRewrite", {
      comment: "Serve index.html for client-side routes (default behaviour only, so API 404s are untouched)",
      runtime: cloudfront.FunctionRuntime.JS_2_0,
      code: cloudfront.FunctionCode.fromInline(
        "function handler(event) {\n  var r = event.request;\n  if (r.uri.indexOf('.') === -1) { r.uri = '/index.html'; }\n  return r;\n}\n"),
    });
    const apiCache = new cloudfront.CachePolicy(this, "ApiCachePolicy", {
      comment: "FloodLens API: short cache, keyed by the API's own query parameters only",
      minTtl: cdk.Duration.seconds(0), defaultTtl: cdk.Duration.minutes(5), maxTtl: cdk.Duration.hours(1),
      queryStringBehavior: cloudfront.CacheQueryStringBehavior.allowList(...API_QUERY_PARAMS),
      headerBehavior: cloudfront.CacheHeaderBehavior.none(),
      cookieBehavior: cloudfront.CacheCookieBehavior.none(),
      enableAcceptEncodingGzip: true, enableAcceptEncodingBrotli: true,
    });
    const mapsHost = `https://maps.geo.${REGION}.amazonaws.com`;
    const security = new cloudfront.ResponseHeadersPolicy(this, "SecurityHeaders", {
      comment: "FloodLens security headers",
      securityHeadersBehavior: {
        strictTransportSecurity: { accessControlMaxAge: cdk.Duration.days(365), includeSubdomains: true, override: true },
        contentTypeOptions: { override: true },
        frameOptions: { frameOption: cloudfront.HeadersFrameOption.DENY, override: true },
        referrerPolicy: { referrerPolicy: cloudfront.HeadersReferrerPolicy.STRICT_ORIGIN_WHEN_CROSS_ORIGIN, override: true },
        contentSecurityPolicy: {
          override: true,
          contentSecurityPolicy: [
            "default-src 'self'", "script-src 'self'", "style-src 'self' 'unsafe-inline'",
            "img-src 'self' data: blob:", `font-src 'self' data: ${mapsHost}`,
            `connect-src 'self' ${mapsHost}`, "worker-src 'self' blob:", "child-src blob:",
            "object-src 'none'", "base-uri 'self'", "frame-ancestors 'none'",
          ].join("; "),
        },
      },
    });
    const apiOrigin = new origins.HttpOrigin(cdk.Fn.select(2, cdk.Fn.split("/", httpApi.apiEndpoint)), {
      protocolPolicy: cloudfront.OriginProtocolPolicy.HTTPS_ONLY,
    });
    const noCache = { cachePolicy: cloudfront.CachePolicy.CACHING_DISABLED, viewerProtocolPolicy: cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
      responseHeadersPolicy: security, compress: true };
    const siteOrigin = origins.S3BucketOrigin.withOriginAccessControl(siteBucket);
    const distribution = new cloudfront.Distribution(this, "Distribution", {
      comment: "FloodLens web app and API",
      defaultRootObject: "index.html",
      // minimumProtocolVersion is intentionally not set: CloudFront ignores it on the default *.cloudfront.net
      // certificate. Enforcing TLS 1.2+ needs a custom domain and an ACM certificate (see docs/deployment).
      httpVersion: cloudfront.HttpVersion.HTTP2_AND_3,
      priceClass: cloudfront.PriceClass.PRICE_CLASS_200, // includes India edge locations
      defaultBehavior: {
        origin: siteOrigin,
        viewerProtocolPolicy: cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
        cachePolicy: cloudfront.CachePolicy.CACHING_OPTIMIZED,
        responseHeadersPolicy: security,
        allowedMethods: cloudfront.AllowedMethods.ALLOW_GET_HEAD,
        compress: true,
        functionAssociations: [{ function: spaRewrite, eventType: cloudfront.FunctionEventType.VIEWER_REQUEST }],
      },
      additionalBehaviors: {
        "/v1/*": {
          origin: apiOrigin,
          viewerProtocolPolicy: cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
          cachePolicy: apiCache,
          responseHeadersPolicy: security,
          allowedMethods: cloudfront.AllowedMethods.ALLOW_GET_HEAD,
          compress: true,
        },
        // deploy-time configuration must never be served stale
        "/config.json": { origin: siteOrigin, ...noCache },
      },
    });

    // ---- Amazon Location: maps-only, referrer-bound browser key -----------------------------------------
    const referers = [`https://${distribution.distributionDomainName}/*`, ...(props.extraReferers ?? [])];
    const mapKey = new location.CfnAPIKey(this, "MapsApiKey", {
      keyName: `floodlens-web-maps-${cdk.Names.uniqueId(this).slice(-8).toLowerCase()}`,
      description: "FloodLens browser basemap key: map actions only, restricted to the site origin",
      restrictions: {
        allowActions: ["geo-maps:GetStyleDescriptor", "geo-maps:GetTile", "geo-maps:GetSprites", "geo-maps:GetGlyphs"],
        allowResources: [`arn:${cdk.Aws.PARTITION}:geo-maps:${REGION}::provider/default`],
        allowReferers: referers,
      },
      ...(props.locationKeyExpireTime
        ? { expireTime: props.locationKeyExpireTime, noExpiry: false } : { noExpiry: true }),
    });

    // CloudFormation does not return an API key's value, so read it back at deploy time. The caller may
    // only describe this one key; response data is kept out of logs. The key is a public browser key by
    // design (map actions, bound to the site origin), and it never appears in source or the JS bundle.
    const helperLogs = (idn: string) => new logs.LogGroup(this, idn, {
      retention: logs.RetentionDays.ONE_MONTH, removalPolicy: cdk.RemovalPolicy.DESTROY, // deploy-time helpers only
    });
    const keyValue = new cr.AwsCustomResource(this, "ReadMapsKey", {
      installLatestAwsSdk: false,
      logGroup: helperLogs("ReadMapsKeyLogs"),
      onUpdate: {
        logging: cr.Logging.withDataHidden(),
        service: "Location", action: "describeKey", parameters: { KeyName: mapKey.keyName },
        physicalResourceId: cr.PhysicalResourceId.of(`${mapKey.keyName}-value`),
      },
      policy: cr.AwsCustomResourcePolicy.fromStatements([new iam.PolicyStatement({
        actions: ["geo:DescribeKey"], resources: [mapKey.attrKeyArn] })]),
    });

    // ---- site content and runtime config ---------------------------------------------------------------
    const assets = new s3deploy.BucketDeployment(this, "DeploySite", {
      sources: [s3deploy.Source.asset(webDist)],
      destinationBucket: siteBucket,
      logGroup: helperLogs("DeploySiteLogs"),
      distribution, distributionPaths: ["/*"],
      prune: false, // never delete objects from the bucket
      cacheControl: [s3deploy.CacheControl.fromString("public, max-age=300")],
    });
    const config = new s3deploy.BucketDeployment(this, "DeployRuntimeConfig", {
      sources: [s3deploy.Source.jsonData("config.json", {
        VITE_LOCATION_API_KEY: keyValue.getResponseField("Key"),
        VITE_LOCATION_REGION: REGION,
        VITE_LOCATION_STYLE: mapStyle,
        VITE_LOCATION_POLITICAL_VIEW: "IND",
      })],
      destinationBucket: siteBucket,
      logGroup: helperLogs("DeployRuntimeConfigLogs"),
      distribution, distributionPaths: ["/config.json"],
      prune: false,
      cacheControl: [s3deploy.CacheControl.noStore()],
    });
    config.node.addDependency(assets);

    // ---- observability -----------------------------------------------------------------------------------
    const topic = new sns.Topic(this, "AlarmTopic", { displayName: "FloodLens alarms" });
    if (props.alarmEmail) topic.addSubscription(new subs.EmailSubscription(props.alarmEmail));
    const act = new cwActions.SnsAction(topic);
    const alarm = (idn: string, metric: cloudwatch.IMetric, threshold: number, desc: string) => {
      const a = new cloudwatch.Alarm(this, idn, {
        metric, threshold, evaluationPeriods: 1, alarmDescription: desc,
        comparisonOperator: cloudwatch.ComparisonOperator.GREATER_THAN_OR_EQUAL_TO_THRESHOLD,
        treatMissingData: cloudwatch.TreatMissingData.NOT_BREACHING,
      });
      a.addAlarmAction(act);
    };
    const five = cdk.Duration.minutes(5);
    alarm("LambdaErrors", apiFn.metricErrors({ period: five }), 1, "API Lambda raised an error");
    alarm("LambdaThrottles", apiFn.metricThrottles({ period: five }), 1, "API Lambda was throttled");
    alarm("LambdaSlow", apiFn.metricDuration({ period: five, statistic: "p95" }), 10_000, "API p95 duration above 10 s");
    alarm("Api5xx", httpApi.metricServerError({ period: five }), 5, "API Gateway returned 5xx responses");

    new cloudwatch.Dashboard(this, "Dashboard", {
      dashboardName: "FloodLens",
      widgets: [[
        new cloudwatch.GraphWidget({ title: "API requests and errors", width: 12, left: [
          httpApi.metricCount({ period: five }), httpApi.metricClientError({ period: five }), httpApi.metricServerError({ period: five })] }),
        new cloudwatch.GraphWidget({ title: "Lambda duration (p50/p95)", width: 12, left: [
          apiFn.metricDuration({ period: five, statistic: "p50" }), apiFn.metricDuration({ period: five, statistic: "p95" })] }),
      ], [
        new cloudwatch.GraphWidget({ title: "Lambda invocations, errors, throttles", width: 12, left: [
          apiFn.metricInvocations({ period: five }), apiFn.metricErrors({ period: five }), apiFn.metricThrottles({ period: five })] }),
        new cloudwatch.GraphWidget({ title: "API latency", width: 12, left: [httpApi.metricLatency({ period: five })] }),
      ]],
    });

    cdk.Tags.of(this).add("Project", "FloodLens");
    cdk.Tags.of(this).add("ManagedBy", "cdk");

    new cdk.CfnOutput(this, "SiteUrl", { value: `https://${distribution.distributionDomainName}` });
    new cdk.CfnOutput(this, "ApiEndpoint", { value: httpApi.apiEndpoint });
    new cdk.CfnOutput(this, "SiteBucketName", { value: siteBucket.bucketName });
    new cdk.CfnOutput(this, "MapsApiKeyArn", { value: mapKey.attrKeyArn }); // the key value is never an output
  }
}
