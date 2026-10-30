import * as crypto from "node:crypto";
import * as fs from "node:fs";
import * as path from "node:path";
import * as cdk from "aws-cdk-lib";
import * as apigw from "aws-cdk-lib/aws-apigateway";
import * as apigwv2 from "aws-cdk-lib/aws-apigatewayv2";
import * as integrations from "aws-cdk-lib/aws-apigatewayv2-integrations";
import * as cloudwatch from "aws-cdk-lib/aws-cloudwatch";
import * as cwActions from "aws-cdk-lib/aws-cloudwatch-actions";
import * as lambda from "aws-cdk-lib/aws-lambda";
import * as location from "aws-cdk-lib/aws-location";
import * as logs from "aws-cdk-lib/aws-logs";
import * as sns from "aws-cdk-lib/aws-sns";
import * as subs from "aws-cdk-lib/aws-sns-subscriptions";
import type { Construct } from "constructs";

export const REGION = "ap-south-1";

export interface FloodLensProps extends cdk.StackProps {
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
   * The key is public by design (map actions, bound to the frontend origin); expiry limits how long a leaked or
   * abused copy stays useful. Rotate by redeploying with a later date before it lapses.
   */
  locationKeyExpireTime: string;
  /**
   * REQUIRED. The browser origins of the Amplify-hosted frontend (and any custom domain), for example
   * https://main.d1a2b3c4d5e6f7.amplifyapp.com. They become the API's CORS allow-list and the map key's
   * referrers. Wildcards are rejected.
   */
  frontendOrigins: string[];
  /** Also allow http://localhost:5173 in API CORS (never in the map key). Off by default. */
  allowLocalDevCors?: boolean;
  /** Amazon Location style served to the browser: Standard | Monochrome | Hybrid | Satellite. Documented for the frontend env. */
  mapStyle?: string;
  /** Sustained requests per second allowed by the API stage (default 10). */
  apiRateLimit?: number;
  /** Burst requests allowed by the API stage (default 20). */
  apiBurstLimit?: number;
  /** Invocations per 5 minutes that raise the traffic-spike alarm (default 3000, about 10 requests/second sustained). */
  invocationAlarmThreshold?: number;
  /** Clock used to validate the key expiry. For tests. */
  now?: Date;
}

/** Longest allowed lifetime of the public map key, counted from synthesis. */
export const MAX_KEY_LIFETIME_DAYS = 90;
export const LOCAL_DEV_ORIGIN = "http://localhost:5173";
export const DEFAULT_API_RATE_LIMIT = 10;
export const DEFAULT_API_BURST_LIMIT = 20;
/** Lambda timeout, and the API integration timeout above it so a Lambda timeout surfaces as the function's own error. */
export const LAMBDA_TIMEOUT_SECONDS = 15;
export const INTEGRATION_TIMEOUT_SECONDS = 20;

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
 * Checks the frontend origins: at least one, each a bare https origin (scheme + host, optional port; no path, query,
 * wildcard or credentials). Returns them de-duplicated, lower-cased, in the given order.
 */
export function validateFrontendOrigins(values: string[] | undefined): string[] {
  if (!values || values.length === 0) {
    throw new Error("frontendOrigins is required (for example -c frontendOrigins=https://main.d1a2b3c4d5e6f7.amplifyapp.com): CORS and the map key must name the real frontend.");
  }
  const out: string[] = [];
  for (const raw of values) {
    const v = raw.trim();
    if (v.includes("*")) throw new Error(`frontend origin "${v}" contains a wildcard; list each origin explicitly.`);
    let u: URL;
    try { u = new URL(v); } catch { throw new Error(`frontend origin "${v}" is not a valid URL.`); }
    if (u.protocol !== "https:") throw new Error(`frontend origin "${v}" must be https.`);
    if (u.username || u.password || u.search || u.hash || (u.pathname !== "/" && u.pathname !== "") || v.endsWith("/")) {
      throw new Error(`frontend origin "${v}" must be a bare origin such as https://example.com (no path, query or trailing slash).`);
    }
    if (!out.includes(u.origin)) out.push(u.origin);
  }
  return out;
}

function requireDir(dir: string, hint: string): string {
  const abs = path.resolve(dir);
  if (!fs.existsSync(abs) || !fs.statSync(abs).isDirectory()) {
    throw new Error(`${abs} does not exist. ${hint}`);
  }
  return abs;
}

function positiveInt(name: string, value: number | undefined, fallback: number): number {
  const v = value ?? fallback;
  if (!Number.isInteger(v) || v < 1) throw new Error(`${name} must be a positive integer, got ${String(value)}`);
  return v;
}

/**
 * FloodLens backend: read-only FastAPI on Lambda behind an API Gateway HTTP API, plus the Amazon Location browser map
 * key and CloudWatch alarms. The React app is NOT in this stack: AWS Amplify Hosting builds and serves it from GitHub.
 * No geospatial processing runs here: the API serves artifacts built offline and shipped inside the Lambda package.
 */
export class FloodLensStack extends cdk.Stack {
  constructor(scope: Construct, id: string, props: FloodLensProps) {
    super(scope, id, { ...props, env: { account: props.env?.account, region: REGION } });

    const alarmEmail = validateAlarmEmail(props.alarmEmail);
    const keyExpiry = validateKeyExpiry(props.locationKeyExpireTime, props.now);
    const origins = validateFrontendOrigins(props.frontendOrigins);
    const lambdaDir = requireDir(props.lambdaDir, "Build it first: python scripts/package_lambda.py");
    const rate = positiveInt("apiRateLimit", props.apiRateLimit, DEFAULT_API_RATE_LIMIT);
    const burst = positiveInt("apiBurstLimit", props.apiBurstLimit, DEFAULT_API_BURST_LIMIT);
    if (burst < rate) throw new Error(`apiBurstLimit (${burst}) must be at least apiRateLimit (${rate}).`);
    const corsOrigins = props.allowLocalDevCors ? [...origins, LOCAL_DEV_ORIGIN] : origins;

    // ---- API: Lambda (FastAPI + Mangum) -------------------------------------------------------------------
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
      timeout: cdk.Duration.seconds(LAMBDA_TIMEOUT_SECONDS),
      logGroup: fnLogs,
      environment: { FLOODLENS_ARTIFACT_DIR: "/var/task/data/api" },
      // No reservedConcurrentExecutions: the owner accepted the shared account pool (quota 10, increase pending).
    });
    // The execution role is only the managed logging permissions CDK adds for logGroup: the function
    // reads files in its own package and calls no other AWS service.

    // ---- API Gateway HTTP API: GET and HEAD under /v1 only; CORS for the Amplify origin(s) ------------------
    const accessLogs = new logs.LogGroup(this, "ApiAccessLogs", {
      retention: logs.RetentionDays.ONE_MONTH, removalPolicy: cdk.RemovalPolicy.RETAIN,
    });
    const httpApi = new apigwv2.HttpApi(this, "HttpApi", {
      apiName: "FloodLensAmplifyApi",
      description: "FloodLens read-only API (GET and HEAD under /v1)",
      createDefaultStage: false, // the $default stage is created below so it can carry throttling and access logs
      corsPreflight: {
        allowOrigins: corsOrigins,
        allowMethods: [apigwv2.CorsHttpMethod.GET, apigwv2.CorsHttpMethod.HEAD, apigwv2.CorsHttpMethod.OPTIONS],
        allowHeaders: ["accept", "content-type", "x-request-id"],
        exposeHeaders: ["x-request-id", "x-floodlens-schema-version", "x-content-sha256", "content-length"],
        maxAge: cdk.Duration.days(1),
        allowCredentials: false,
      },
    });
    const integration = new integrations.HttpLambdaIntegration("ApiIntegration", apiFn, {
      payloadFormatVersion: apigwv2.PayloadFormatVersion.VERSION_2_0,
      timeout: cdk.Duration.seconds(INTEGRATION_TIMEOUT_SECONDS), // above the Lambda timeout; HTTP API maximum is 30 s
    });
    httpApi.addRoutes({
      path: "/v1/{proxy+}",
      methods: [apigwv2.HttpMethod.GET, apigwv2.HttpMethod.HEAD],
      integration,
    });
    // Nothing else is routed: /docs, /openapi.json and the bare /v1 root return the gateway's 404 and never reach Lambda.
    // The query string is passed through untouched (payload format 2.0 rawQueryString); there is no cache and no allow-list.
    new apigwv2.HttpStage(this, "DefaultStage", {
      httpApi,
      stageName: "$default",
      autoDeploy: true,
      throttle: { rateLimit: rate, burstLimit: burst },
      accessLogSettings: {
        destination: new apigwv2.LogGroupLogDestination(accessLogs),
        // No client IP, user agent, headers or query string: access logs carry operational fields only.
        format: apigw.AccessLogFormat.custom(JSON.stringify({
          requestId: "$context.requestId", requestTime: "$context.requestTime", httpMethod: "$context.httpMethod",
          routeKey: "$context.routeKey", path: "$context.path", status: "$context.status",
          protocol: "$context.protocol", responseLength: "$context.responseLength",
          responseLatency: "$context.responseLatency", integrationLatency: "$context.integrationLatency",
          integrationStatus: "$context.integrationStatus", errorMessage: "$context.error.message",
        })),
      },
    });

    // ---- Amazon Location: maps-only, referrer-bound browser key with a mandatory expiry -------------------
    // CloudFormation does not return an API key's value. The owner reads it once after deployment
    // (aws location describe-key) and enters it as the Amplify environment variable VITE_LOCATION_API_KEY. It is
    // a public browser key by design (map actions only, bound to the frontend origin) and never appears in source.
    const keySuffix = crypto.createHash("sha256").update(`${this.stackName}/MapsApiKey`).digest("hex").slice(0, 8);
    const mapKey = new location.CfnAPIKey(this, "MapsApiKey", {
      keyName: `floodlens-web-maps-${keySuffix}`,
      description: "FloodLens browser basemap key: map actions only, restricted to the Amplify frontend origin, expires",
      restrictions: {
        allowActions: ["geo-maps:GetStyleDescriptor", "geo-maps:GetTile", "geo-maps:GetSprites", "geo-maps:GetGlyphs"],
        allowResources: [`arn:${cdk.Aws.PARTITION}:geo-maps:${REGION}::provider/default`],
        allowReferers: origins.map((o) => `${o}/*`),
      },
      expireTime: keyExpiry,
      noExpiry: false,
    });

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
    // Early warning for abuse or a traffic surge (alarm only; the stage throttle is what limits the request rate).
    alarm("LambdaTrafficSpike", apiFn.metricInvocations({ period: five }), props.invocationAlarmThreshold ?? 3000,
      "API Lambda invocations in 5 minutes are unusually high");
    alarm("Api5xx", httpApi.metricServerError({ period: five }), 1, "API Gateway returned a 5xx response");

    new cloudwatch.Dashboard(this, "Dashboard", {
      dashboardName: "FloodLensAmplify",
      widgets: [[
        new cloudwatch.GraphWidget({ title: "Lambda invocations, errors, throttles", width: 12, left: [
          apiFn.metricInvocations({ period: five }), apiFn.metricErrors({ period: five }), apiFn.metricThrottles({ period: five })] }),
        new cloudwatch.GraphWidget({ title: "Lambda duration (p50/p95)", width: 12, left: [
          apiFn.metricDuration({ period: five, statistic: "p50" }), apiFn.metricDuration({ period: five, statistic: "p95" })] }),
      ], [
        new cloudwatch.GraphWidget({ title: "API requests, 4xx, 5xx", width: 12, left: [
          httpApi.metricCount({ period: five }), httpApi.metricClientError({ period: five }), httpApi.metricServerError({ period: five })] }),
        new cloudwatch.GraphWidget({ title: "Lambda concurrent executions (max)", width: 12, left: [
          apiFn.metric("ConcurrentExecutions", { period: five, statistic: "Maximum" })] }),
      ]],
    });

    cdk.Tags.of(this).add("Project", "FloodLens");
    cdk.Tags.of(this).add("ManagedBy", "cdk");

    new cdk.CfnOutput(this, "ApiBaseUrl", {
      value: httpApi.apiEndpoint,
      description: "Set as the Amplify environment variable VITE_API_BASE_URL (no trailing slash).",
    });
    new cdk.CfnOutput(this, "MapsApiKeyName", { value: mapKey.keyName });
    new cdk.CfnOutput(this, "MapsApiKeyArn", { value: mapKey.attrKeyArn }); // the key value is never an output
    new cdk.CfnOutput(this, "MapStyle", { value: props.mapStyle ?? "Monochrome", description: "Value for VITE_LOCATION_STYLE." });
  }
}
