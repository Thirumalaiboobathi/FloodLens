import * as fs from "node:fs";
import * as path from "node:path";
import * as cdk from "aws-cdk-lib";
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
  /**
   * REQUIRED. Receives CloudWatch alarm notifications. The stack creates the SNS e-mail subscription, but it stays
   * "pending confirmation" until the recipient clicks the link in the confirmation e-mail; an unconfirmed
   * subscription receives nothing.
   */
  alarmEmail: string;
  /**
   * REQUIRED. ISO-8601 expiry for the browser map key: in the future and at most MAX_KEY_LIFETIME_DAYS away.
   * The key is public by design (map actions, bound to the site origin); expiry limits how long a leaked or
   * abused copy stays useful. Rotate by redeploying with a later date before it lapses.
   */
  locationKeyExpireTime: string;
  /** Amazon Location style served to the browser: Standard | Monochrome | Hybrid | Satellite. */
  mapStyle?: string;
  /** Extra origins allowed to use the map key (for example a custom domain). */
  extraReferers?: string[];
  /**
   * Optional Lambda reserved concurrency. Unset by default: new accounts can have a low concurrency quota
   * and a reservation that leaves under 100 unreserved executions makes the deployment fail. A Function URL has no
   * API Gateway stage throttle, so this is the only hard cap on concurrent executions.
   */
  reservedConcurrency?: number;
  /** Invocations per 5 minutes that raise the traffic-spike alarm (default 3000, about 10 requests/second sustained). */
  invocationAlarmThreshold?: number;
  /** Clock used to validate the key expiry. For tests. */
  now?: Date;
}

/** Longest allowed lifetime of the public map key, counted from synthesis. */
export const MAX_KEY_LIFETIME_DAYS = 90;

/** Checks the map-key expiry (required, ISO-8601 with a time zone, in the future, within MAX_KEY_LIFETIME_DAYS). Returns it as UTC. */
export function validateKeyExpiry(value: string | undefined, now: Date = new Date()): string {
  if (!value) {
    throw new Error("locationKeyExpireTime is required (for example -c locationKeyExpireTime=2027-01-15T00:00:00Z): the map key is never created without an expiry.");
  }
  const m = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})(?::(\d{2})(?:\.\d+)?)?(Z|[+-](\d{2}):(\d{2}))$/.exec(value);
  if (!m) {
    throw new Error(`locationKeyExpireTime "${value}" is not an ISO-8601 timestamp with a time zone (for example 2027-01-15T00:00:00Z).`);
  }
  // Date.parse rolls impossible dates over (2026-02-30 becomes March 2), so check the calendar fields explicitly.
  const [y, mo, d, h, mi, sec] = [m[1], m[2], m[3], m[4], m[5], m[6] ?? "0"].map(Number);
  const daysInMonth = new Date(Date.UTC(y, mo, 0)).getUTCDate();
  if (mo < 1 || mo > 12 || d < 1 || d > daysInMonth || h > 23 || mi > 59 || sec > 59 || Number(m[8] ?? 0) > 23 || Number(m[9] ?? 0) > 59) {
    throw new Error(`locationKeyExpireTime "${value}" is not a valid date.`);
  }
  const t = new Date(value);
  if (Number.isNaN(t.getTime())) throw new Error(`locationKeyExpireTime "${value}" is not a valid date.`);
  if (t.getTime() <= now.getTime()) throw new Error(`locationKeyExpireTime ${value} is not in the future.`);
  const limit = now.getTime() + MAX_KEY_LIFETIME_DAYS * 86_400_000;
  if (t.getTime() > limit) {
    throw new Error(`locationKeyExpireTime ${value} is more than ${MAX_KEY_LIFETIME_DAYS} days away (latest allowed ${new Date(limit).toISOString()}).`);
  }
  return t.toISOString().replace(/\.\d{3}Z$/, "Z");
}

/** Checks the alarm recipient (required, one plausible e-mail address). */
export function validateAlarmEmail(value: string | undefined): string {
  if (!value) throw new Error("alarmEmail is required (for example -c alarmEmail=owner@example.com): alarms must have a recipient.");
  if (!/^[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}$/.test(value)) {
    throw new Error(`alarmEmail "${value}" is not a valid e-mail address.`);
  }
  return value;
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
 * FloodLens: static React app (private S3 + CloudFront OAC), read-only FastAPI on Lambda reached only through
 * CloudFront (Lambda Function URL, AWS_IAM, CloudFront origin access control), Amazon Location map key,
 * CloudWatch alarms. No geospatial processing runs here: the API serves artifacts built offline and shipped
 * inside the Lambda package. There is no API Gateway.
 */
export class FloodLensStack extends cdk.Stack {
  constructor(scope: Construct, id: string, props: FloodLensProps) {
    super(scope, id, { ...props, env: { account: props.env?.account, region: REGION } });

    const alarmEmail = validateAlarmEmail(props.alarmEmail);
    const keyExpiry = validateKeyExpiry(props.locationKeyExpireTime, props.now);
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

    // ---- API: Lambda (FastAPI + Mangum) behind a Function URL that only CloudFront may call -------------
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

    // AWS_IAM: a request without a valid SigV4 signature from a principal the resource policy allows is
    // rejected by the Lambda service before the function runs. No CORS block: the browser only talks to CloudFront.
    const fnUrl = apiFn.addFunctionUrl({
      authType: lambda.FunctionUrlAuthType.AWS_IAM,
      invokeMode: lambda.InvokeMode.BUFFERED, // responses are complete documents/rasters under 6 MB
    });

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
    // CloudFront signs each origin request with SigV4 (OAC, signing behaviour "always"). CDK also adds the two
    // Lambda resource-policy statements (InvokeFunctionUrl and InvokeFunction), each limited to this distribution's ARN.
    // The viewer Host header is not forwarded (no origin request policy), which SigV4 to a Function URL requires.
    const apiOrigin = origins.FunctionUrlOrigin.withOriginAccessControl(fnUrl, {
      readTimeout: cdk.Duration.seconds(20), // above the 15 s function timeout so Lambda errors surface as the function's own response
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

    // ---- Amazon Location: maps-only, referrer-bound browser key with a mandatory expiry -------------------
    const referers = [`https://${distribution.distributionDomainName}/*`, ...(props.extraReferers ?? [])];
    const mapKey = new location.CfnAPIKey(this, "MapsApiKey", {
      keyName: `floodlens-web-maps-${cdk.Names.uniqueId(this).slice(-8).toLowerCase()}`,
      description: "FloodLens browser basemap key: map actions only, restricted to the site origin, expires",
      restrictions: {
        allowActions: ["geo-maps:GetStyleDescriptor", "geo-maps:GetTile", "geo-maps:GetSprites", "geo-maps:GetGlyphs"],
        allowResources: [`arn:${cdk.Aws.PARTITION}:geo-maps:${REGION}::provider/default`],
        allowReferers: referers,
      },
      expireTime: keyExpiry,
      noExpiry: false,
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
    topic.addSubscription(new subs.EmailSubscription(alarmEmail)); // stays pending until the recipient confirms
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
    // Replaces the API Gateway throttle as the early warning for abuse of the API path (alarm only; it does not block traffic).
    alarm("LambdaTrafficSpike", apiFn.metricInvocations({ period: five }), props.invocationAlarmThreshold ?? 3000,
      "API Lambda invocations in 5 minutes are unusually high");

    new cloudwatch.Dashboard(this, "Dashboard", {
      dashboardName: "FloodLens",
      widgets: [[
        new cloudwatch.GraphWidget({ title: "Lambda invocations, errors, throttles", width: 12, left: [
          apiFn.metricInvocations({ period: five }), apiFn.metricErrors({ period: five }), apiFn.metricThrottles({ period: five })] }),
        new cloudwatch.GraphWidget({ title: "Lambda duration (p50/p95)", width: 12, left: [
          apiFn.metricDuration({ period: five, statistic: "p50" }), apiFn.metricDuration({ period: five, statistic: "p95" })] }),
      ], [
        new cloudwatch.GraphWidget({ title: "Lambda concurrent executions (max)", width: 12, left: [
          apiFn.metric("ConcurrentExecutions", { period: five, statistic: "Maximum" })] }),
      ]],
    });

    cdk.Tags.of(this).add("Project", "FloodLens");
    cdk.Tags.of(this).add("ManagedBy", "cdk");

    new cdk.CfnOutput(this, "SiteUrl", { value: `https://${distribution.distributionDomainName}` });
    new cdk.CfnOutput(this, "ApiFunctionUrl", {
      value: fnUrl.url,
      description: "Not a public entry point: unsigned requests are rejected (AWS_IAM). Used only to confirm that.",
    });
    new cdk.CfnOutput(this, "SiteBucketName", { value: siteBucket.bucketName });
    new cdk.CfnOutput(this, "MapsApiKeyArn", { value: mapKey.attrKeyArn }); // the key value is never an output
  }
}
