import { execFileSync } from "node:child_process";
import * as fs from "node:fs";
import * as os from "node:os";
import * as path from "node:path";
import * as cdk from "aws-cdk-lib";
import { Match, Template } from "aws-cdk-lib/assertions";
import { beforeAll, describe, expect, it } from "vitest";
import {
  DEFAULT_API_BURST_LIMIT, DEFAULT_API_RATE_LIMIT, FloodLensStack, INTEGRATION_TIMEOUT_SECONDS, LAMBDA_TIMEOUT_SECONDS,
  LOCAL_DEV_ORIGIN, validateAlarmEmail, validateFrontendOrigins, validateMapsApiKeyArn,
} from "../lib/floodlens-stack";

// A tiny stand-in for the real Lambda package: the stack only needs the directory to exist.
let tmp: string;
let template: Template;
let json: string;
const ORIGIN = "https://main.d1a2b3c4d5e6f7.amplifyapp.com";
const KEY_ARN = "arn:aws:geo:ap-south-1:675613597178:api-key/floodlens";
type Props = ConstructorParameters<typeof FloodLensStack>[2];
const baseProps = (): Props => ({
  lambdaDir: path.join(tmp, "lambda"), alarmEmail: "ops@example.org", mapsApiKeyArn: KEY_ARN,
  frontendOrigins: [ORIGIN],
});
const stackWith = (extra: Partial<Props> = {}) =>
  Template.fromStack(new FloodLensStack(new cdk.App(), "T", { ...baseProps(), ...extra }));
const resources = (t: Template) => Object.values(t.toJSON().Resources) as { Type: string; Properties: Record<string, any> }[]; // eslint-disable-line @typescript-eslint/no-explicit-any

beforeAll(() => {
  tmp = fs.mkdtempSync(path.join(os.tmpdir(), "floodlens-cdk-"));
  fs.mkdirSync(path.join(tmp, "lambda"));
  fs.writeFileSync(path.join(tmp, "lambda", "handler.py"), "");
  template = stackWith();
  json = JSON.stringify(template.toJSON());
});

describe("removed architecture", () => {
  it("has no CloudFront, S3 site bucket, Function URL, OAC, bucket deployment or custom-resource key reader", () => {
    const types = new Set(resources(template).map((r) => r.Type));
    for (const t of types) {
      expect(t, t).not.toMatch(/^AWS::CloudFront::|^AWS::S3::|^Custom::|^AWS::Lambda::Url$|^AWS::Lambda::LayerVersion$/);
    }
    expect(json).not.toMatch(/cloudfront\.amazonaws\.com|FunctionUrl|CachePolicy|OriginAccessControl/i);
  });

  it("creates the API on API Gateway v2 (HTTP API) only, never a REST API", () => {
    template.resourceCountIs("AWS::ApiGatewayV2::Api", 1);
    template.hasResourceProperties("AWS::ApiGatewayV2::Api", { ProtocolType: "HTTP" });
    expect(resources(template).filter((r) => r.Type.startsWith("AWS::ApiGateway::"))).toHaveLength(0);
  });
});

describe("api gateway http api", () => {
  it("proxies GET and HEAD under /v1 to Lambda with payload format 2.0 (AWS_PROXY) and nothing else", () => {
    template.hasResourceProperties("AWS::ApiGatewayV2::Integration", {
      IntegrationType: "AWS_PROXY", PayloadFormatVersion: "2.0", TimeoutInMillis: INTEGRATION_TIMEOUT_SECONDS * 1000,
    });
    const routes = Object.values(template.findResources("AWS::ApiGatewayV2::Route")).map((r) => r.Properties.RouteKey).sort();
    expect(routes).toEqual(["GET /v1/{proxy+}", "HEAD /v1/{proxy+}"]);
    // no $default catch-all, no ANY route, no docs or OpenAPI route
    expect(routes.join()).not.toMatch(/\$default|ANY|docs|openapi|POST|PUT|DELETE|PATCH/);
  });

  it("sets an integration timeout above the Lambda timeout and within the HTTP API maximum of 30 s", () => {
    expect(INTEGRATION_TIMEOUT_SECONDS).toBeGreaterThan(LAMBDA_TIMEOUT_SECONDS);
    expect(INTEGRATION_TIMEOUT_SECONDS).toBeLessThanOrEqual(30);
  });

  it("has no authorizer, no mapping or request transform that could drop or rename a query parameter", () => {
    expect(json).not.toMatch(/Authorizer|RequestParameters|RequestTemplates|ParameterMapping/);
    expect(resources(template).filter((r) => r.Type === "AWS::ApiGatewayV2::Integration")
      .every((r) => r.Properties.RequestParameters === undefined)).toBe(true);
  });

  it("uses the $default stage, auto-deployed, throttled, with structured access logs and no execute-api opt-out", () => {
    template.hasResourceProperties("AWS::ApiGatewayV2::Stage", Match.objectLike({
      StageName: "$default", AutoDeploy: true,
      DefaultRouteSettings: { ThrottlingRateLimit: DEFAULT_API_RATE_LIMIT, ThrottlingBurstLimit: DEFAULT_API_BURST_LIMIT },
      AccessLogSettings: Match.objectLike({ DestinationArn: Match.anyValue(), Format: Match.stringLikeRegexp("requestId") }),
    }));
    const api = Object.values(template.findResources("AWS::ApiGatewayV2::Api"))[0].Properties;
    expect(api.DisableExecuteApiEndpoint).toBeUndefined();
  });

  it("makes the throttle configurable and rejects nonsense", () => {
    stackWith({ apiRateLimit: 5, apiBurstLimit: 8 }).hasResourceProperties("AWS::ApiGatewayV2::Stage", {
      DefaultRouteSettings: { ThrottlingRateLimit: 5, ThrottlingBurstLimit: 8 } });
    expect(() => stackWith({ apiRateLimit: 0 })).toThrow(/positive integer/);
    expect(() => stackWith({ apiRateLimit: 30, apiBurstLimit: 10 })).toThrow(/at least apiRateLimit/);
  });

  it("keeps access logs free of client IP, user agent, headers and query string", () => {
    const stage = Object.values(template.findResources("AWS::ApiGatewayV2::Stage"))[0].Properties;
    const fmt: string = stage.AccessLogSettings.Format;
    for (const field of ["requestId", "httpMethod", "routeKey", "status", "responseLatency", "integrationStatus"]) expect(fmt).toContain(field);
    expect(fmt).not.toMatch(/sourceIp|userAgent|requestHeader|rawQueryString|queryString|authorization|identity/i);
  });

  it("does not cache API responses anywhere", () => {
    expect(json).not.toMatch(/CacheClusterEnabled|CachingEnabled|CachePolicy|CacheTtl/);
  });
});

describe("cors", () => {
  const cors = (t: Template) => Object.values(t.findResources("AWS::ApiGatewayV2::Api"))[0].Properties.CorsConfiguration;

  it("allows exactly the frontend origin, GET/HEAD/OPTIONS, a short header list, and no credentials", () => {
    const c = cors(template);
    expect(c.AllowOrigins).toEqual([ORIGIN]);
    expect([...c.AllowMethods].sort()).toEqual(["GET", "HEAD", "OPTIONS"]);
    expect(c.AllowHeaders).toEqual(["accept", "content-type", "x-request-id"]);
    expect(c.AllowCredentials).toBe(false);
    expect(c.MaxAge).toBe(86400);
    expect(c.ExposeHeaders).toEqual(expect.arrayContaining(["x-floodlens-schema-version", "x-content-sha256", "x-request-id"]));
    expect(JSON.stringify(c)).not.toContain('"*"');
  });

  it("adds the local dev origin only when explicitly asked", () => {
    expect(cors(template).AllowOrigins).not.toContain(LOCAL_DEV_ORIGIN);
    const t = stackWith({ allowLocalDevCors: true });
    expect(cors(t).AllowOrigins).toEqual([ORIGIN, LOCAL_DEV_ORIGIN]);
  });

  it("supports several explicit origins (for example a custom domain)", () => {
    const t = stackWith({ frontendOrigins: [ORIGIN, "https://floodlens.example.org"] });
    expect(cors(t).AllowOrigins).toEqual([ORIGIN, "https://floodlens.example.org"]);
  });

  it("validates origins: required, https, bare, no wildcard", () => {
    expect(() => validateFrontendOrigins(undefined)).toThrow(/required/);
    expect(() => validateFrontendOrigins([])).toThrow(/required/);
    for (const bad of ["*", "https://*.amplifyapp.com", "http://example.org", "https://example.org/", "https://example.org/app",
      "https://example.org?x=1", "https://user:pw@example.org", "not a url", "localhost:5173"]) {
      expect(() => validateFrontendOrigins([bad]), bad).toThrow();
    }
    expect(validateFrontendOrigins([ORIGIN.toUpperCase().replace("HTTPS", "https"), ORIGIN])).toEqual([ORIGIN]);
    expect(() => new FloodLensStack(new cdk.App(), "O", { ...baseProps(), frontendOrigins: undefined as unknown as string[] }))
      .toThrow(/frontendOrigins is required/);
  });
});

describe("lambda", () => {
  it("runs Python 3.13 on arm64 with no secrets and only the artifact directory configured", () => {
    template.hasResourceProperties("AWS::Lambda::Function", Match.objectLike({
      Runtime: "python3.13", Architectures: ["arm64"], Handler: "floodlens_api.lambda_handler.handler",
      MemorySize: 1024, Timeout: LAMBDA_TIMEOUT_SECONDS,
      Environment: { Variables: { FLOODLENS_ARTIFACT_DIR: "/var/task/data/api" } },
    }));
    template.resourceCountIs("AWS::Lambda::Function", 1); // no deploy-time helper functions any more
  });

  it("does not reserve concurrency, and the app refuses a reservedConcurrency context", () => {
    expect(json).not.toContain("ReservedConcurrentExecutions");
    expect((stackWith() as Template).findResources("AWS::Lambda::Function")).toBeDefined();
  });

  it("gives the function no data-plane permissions: logging only", () => {
    const policies = Object.values(template.findResources("AWS::IAM::Policy"))
      .filter((p) => JSON.stringify(p.Properties.Roles).includes("ApiFunctionServiceRole"));
    for (const p of policies) {
      for (const st of p.Properties.PolicyDocument.Statement) {
        const acts = ([] as string[]).concat(st.Action);
        expect(acts.every((a) => a.startsWith("logs:") || a.startsWith("xray:"))).toBe(true);
      }
    }
    const role = Object.entries(template.findResources("AWS::IAM::Role")).find(([k]) => k.startsWith("ApiFunctionServiceRole"));
    expect(role).toBeDefined();
    expect(JSON.stringify(role![1])).not.toContain("s3:");
    template.resourceCountIs("AWS::IAM::Role", 1);
  });

  it("allows invocation only by API Gateway, scoped to this API's execution ARN, and never by a public principal", () => {
    const perms = Object.values(template.findResources("AWS::Lambda::Permission")).map((p) => p.Properties);
    expect(perms.length).toBeGreaterThanOrEqual(1);
    for (const p of perms) {
      expect(p.Action).toBe("lambda:InvokeFunction");
      expect(p.Principal).toBe("apigateway.amazonaws.com");
      const arn = JSON.stringify(p.SourceArn);
      expect(arn).toContain("execute-api");
      expect(arn).toContain("HttpApi"); // the API's own id, not a wildcard
      expect(arn).toContain("/v1/{proxy+}"); // only the /v1 route on this API id, never the whole API
    }
    expect(JSON.stringify(perms)).not.toContain('"Principal":"*"');
  });

  it("has no Function URL at all", () => {
    template.resourceCountIs("AWS::Lambda::Url", 0);
    expect(json).not.toContain("InvokeFunctionUrl");
  });
});

describe("amazon location map key (owned outside the stack)", () => {
  it("creates no Location resource and no custom resource: the console-created key is only referenced", () => {
    expect(resources(template).filter((r) => r.Type.startsWith("AWS::Location::"))).toHaveLength(0);
    expect(json).not.toMatch(/AWS::Location|describeKey|DescribeKey|AWSCDK|"geo:[A-Za-z]|geo-maps:/);
  });

  it("records only the key ARN and name in outputs, never a key value", () => {
    const out = template.toJSON().Outputs;
    expect(Object.keys(out).sort()).toEqual(["ApiBaseUrl", "MapStyle", "MapsApiKeyArn", "MapsApiKeyName"]);
    expect(out.MapsApiKeyArn.Value).toBe(KEY_ARN);
    expect(out.MapsApiKeyName.Value).toBe("floodlens");
    expect(json).not.toMatch(/v1\.public\./);
  });

  it("accepts only an api-key ARN in this region and rejects a missing, foreign or malformed one", () => {
    expect(validateMapsApiKeyArn(KEY_ARN)).toEqual({ arn: KEY_ARN, name: "floodlens" });
    expect(() => validateMapsApiKeyArn(undefined)).toThrow(/mapsApiKeyArn is required/);
    for (const bad of ["floodlens", "arn:aws:geo:us-east-1:675613597178:api-key/floodlens", "arn:aws:geo:ap-south-1:6756:api-key/floodlens",
      "arn:aws:geo:ap-south-1:675613597178:map/floodlens", "arn:aws:geo:ap-south-1:675613597178:api-key/", "arn:aws:geo:ap-south-1:675613597178:api-key/*"]) {
      expect(() => validateMapsApiKeyArn(bad), bad).toThrow(/not an Amazon Location key ARN/);
    }
    expect(() => new FloodLensStack(new cdk.App(), "N", { ...baseProps(), mapsApiKeyArn: undefined as unknown as string }))
      .toThrow(/mapsApiKeyArn is required/);
  });
});

describe("mandatory deployment settings", () => {
  it("rejects a missing or malformed alarm e-mail", () => {
    expect(() => validateAlarmEmail(undefined)).toThrow(/required/);
    expect(() => validateAlarmEmail("")).toThrow(/required/);
    for (const bad of ["owner", "owner@", "@example.org", "a b@example.org", "owner@example", "a@b@example.org"]) {
      expect(() => validateAlarmEmail(bad), bad).toThrow(/valid e-mail/);
    }
    expect(() => new FloodLensStack(new cdk.App(), "M", { ...baseProps(), alarmEmail: undefined as unknown as string }))
      .toThrow(/alarmEmail is required/);
    expect(validateAlarmEmail("first.last+floodlens@example.co.in")).toBe("first.last+floodlens@example.co.in");
  });

  it("fails the CDK app when a required setting is missing or a reservation is passed", () => {
    const run = (ctx: Record<string, string>): string => {
      try {
        execFileSync(process.execPath, [path.resolve(__dirname, "../node_modules/tsx/dist/cli.mjs"), path.resolve(__dirname, "../bin/floodlens.ts")], {
          env: { ...process.env, CDK_CONTEXT_JSON: JSON.stringify(ctx), CDK_OUTDIR: path.join(tmp, "out") },
          stdio: "pipe", cwd: path.resolve(__dirname, ".."),
        });
        return "";
      } catch (e) { return String((e as { stderr?: Buffer }).stderr ?? e); }
    };
    const ok = { alarmEmail: "ops@example.org", mapsApiKeyArn: KEY_ARN, frontendOrigins: ORIGIN };
    expect(run({ mapsApiKeyArn: KEY_ARN, frontendOrigins: ORIGIN })).toMatch(/alarmEmail is required/);
    expect(run({ alarmEmail: "ops@example.org", frontendOrigins: ORIGIN })).toMatch(/mapsApiKeyArn is required/);
    expect(run({ alarmEmail: "ops@example.org", mapsApiKeyArn: KEY_ARN })).toMatch(/frontendOrigins is required/);
    expect(run({ ...ok, frontendOrigins: "https://*.amplifyapp.com" })).toMatch(/wildcard/);
    expect(run({ ...ok, reservedConcurrency: "10" })).toMatch(/reservedConcurrency is not supported/);
  }, 90_000);
});

describe("observability and safety", () => {
  it("alarms on Lambda errors, throttles, latency, traffic spikes and API 5xx", () => {
    const alarms = Object.values(template.findResources("AWS::CloudWatch::Alarm")).map((a) => a.Properties.MetricName);
    expect(alarms.sort()).toEqual(["5xx", "Duration", "Errors", "Invocations", "Throttles"]);
    template.hasResourceProperties("AWS::CloudWatch::Alarm", { MetricName: "Invocations", Threshold: 3000, Period: 300 });
    stackWith({ invocationAlarmThreshold: 600 }).hasResourceProperties("AWS::CloudWatch::Alarm", { MetricName: "Invocations", Threshold: 600 });
    template.resourceCountIs("AWS::CloudWatch::Dashboard", 1);
  });

  it("always subscribes the alarm e-mail to the SNS topic, and every alarm notifies that topic", () => {
    template.resourceCountIs("AWS::SNS::Subscription", 1);
    template.hasResourceProperties("AWS::SNS::Subscription", { Protocol: "email", Endpoint: "ops@example.org" });
    for (const a of Object.values(template.findResources("AWS::CloudWatch::Alarm"))) {
      expect(JSON.stringify(a.Properties.AlarmActions)).toContain("AlarmTopic");
    }
    stackWith({ alarmEmail: "other@example.org" }).hasResourceProperties("AWS::SNS::Subscription", { Endpoint: "other@example.org" });
  });

  it("writes no AWS access key, secret or token anywhere", () => {
    expect(json).not.toMatch(/AKIA[0-9A-Z]{16}/);
    expect(json).not.toMatch(/ASIA[0-9A-Z]{16}/);
    expect(json).not.toMatch(/aws_secret_access_key|SecretAccessKey|aws_session_token|EARTHDATA/i);
  });

  it("does not create destructive or billing resources, IAM users, or Amplify apps", () => {
    const types = new Set(resources(template).map((r) => r.Type));
    for (const t of types) expect(t).not.toMatch(/Budgets|Billing|Organizations|IAM::User$|IAM::AccessKey|Amplify/);
  });

  it("gives every log group one-month retention; the API function and access logs are retained on delete", () => {
    const groups = Object.values(template.findResources("AWS::Logs::LogGroup"));
    expect(groups).toHaveLength(2); // function logs, API access logs
    for (const g of groups) {
      expect(g.Properties.RetentionInDays).toBe(30);
      expect(g.DeletionPolicy).toBe("Retain");
    }
  });

  it("fails clearly when the Lambda package is missing", () => {
    expect(() => new FloodLensStack(new cdk.App(), "Y", { ...baseProps(), lambdaDir: path.join(tmp, "nope") })).toThrow(/package_lambda\.py/);
  });

  it("creates the stack under an identity distinct from the rolled-back FloodLens stack", async () => {
    const src = fs.readFileSync(path.resolve(__dirname, "../bin/floodlens.ts"), "utf-8");
    expect(src).toMatch(/STACK_NAME = "FloodLensAmplify"/);
    expect(src).not.toMatch(/new FloodLensStack\(app, "FloodLens"/);
  });
});
