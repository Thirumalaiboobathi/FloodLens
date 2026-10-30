import { execFileSync } from "node:child_process";
import * as fs from "node:fs";
import * as os from "node:os";
import * as path from "node:path";
import * as cdk from "aws-cdk-lib";
import { Match, Template } from "aws-cdk-lib/assertions";
import { beforeAll, describe, expect, it } from "vitest";
import {
  API_QUERY_PARAMS, FloodLensStack, MAX_KEY_LIFETIME_DAYS, REGION, validateAlarmEmail, validateKeyExpiry,
} from "../lib/floodlens-stack";

// Tiny stand-ins for the real web build and Lambda package: the stack only needs directories to exist.
let tmp: string;
let template: Template;
let json: string;
const DAY = 86_400_000;
const inDays = (n: number) => new Date(Date.now() + n * DAY).toISOString();
type Props = ConstructorParameters<typeof FloodLensStack>[2];
const baseProps = (): Props => ({
  webDistDir: path.join(tmp, "web"), lambdaDir: path.join(tmp, "lambda"),
  alarmEmail: "ops@example.org", locationKeyExpireTime: inDays(60),
});
const stackWith = (extra: Partial<Props> = {}) =>
  Template.fromStack(new FloodLensStack(new cdk.App(), "T", { ...baseProps(), ...extra }));

beforeAll(() => {
  tmp = fs.mkdtempSync(path.join(os.tmpdir(), "floodlens-cdk-"));
  fs.mkdirSync(path.join(tmp, "web"));
  fs.writeFileSync(path.join(tmp, "web", "index.html"), "<!doctype html>");
  fs.mkdirSync(path.join(tmp, "lambda"));
  fs.writeFileSync(path.join(tmp, "lambda", "handler.py"), "");
  template = stackWith();
  json = JSON.stringify(template.toJSON());
});

describe("storage", () => {
  it("has a private, encrypted, TLS-only, retained site bucket and no other bucket is public", () => {
    template.hasResourceProperties("AWS::S3::Bucket", {
      PublicAccessBlockConfiguration: { BlockPublicAcls: true, BlockPublicPolicy: true, IgnorePublicAcls: true, RestrictPublicBuckets: true },
      BucketEncryption: Match.objectLike({ ServerSideEncryptionConfiguration: Match.anyValue() }),
      VersioningConfiguration: { Status: "Enabled" },
    });
    for (const b of Object.values(template.findResources("AWS::S3::Bucket"))) {
      expect(b.DeletionPolicy).toBe("Retain");
      expect(b.Properties.PublicAccessBlockConfiguration).toBeDefined();
    }
    template.hasResourceProperties("AWS::S3::BucketPolicy", {
      PolicyDocument: { Statement: Match.arrayWith([Match.objectLike({
        Effect: "Deny", Action: "s3:*", Condition: { Bool: { "aws:SecureTransport": "false" } } })]) },
    });
  });

  it("grants bucket reads only to CloudFront through origin access control, never to a public principal", () => {
    expect(Object.values(template.findResources("AWS::CloudFront::Distribution"))[0]
      .Properties.DistributionConfig.Origins.filter((o: { S3OriginConfig?: unknown }) => o.S3OriginConfig)).toHaveLength(1);
    for (const p of Object.values(template.findResources("AWS::S3::BucketPolicy"))) {
      for (const st of p.Properties.PolicyDocument.Statement) {
        if (st.Effect !== "Allow") continue;
        expect(JSON.stringify(st.Principal)).not.toBe(JSON.stringify({ AWS: "*" }));
        expect(st.Principal).not.toBe("*");
      }
    }
    expect(json).toContain("cloudfront.amazonaws.com");
    expect(json).toContain("AWS:SourceArn");
  });
});

describe("api and lambda", () => {
  it("runs Python 3.13 on arm64 with no secrets and only the artifact directory configured", () => {
    template.hasResourceProperties("AWS::Lambda::Function", Match.objectLike({
      Runtime: "python3.13", Architectures: ["arm64"], Handler: "floodlens_api.lambda_handler.handler",
      MemorySize: 1024, Timeout: 15,
      Environment: { Variables: { FLOODLENS_ARTIFACT_DIR: "/var/task/data/api" } },
    }));
  });

  it("gives the API function no data-plane permissions: no S3, no wildcard resource, no inline admin", () => {
    const roles = template.findResources("AWS::IAM::Role");
    const apiRole = Object.entries(roles).find(([k]) => k.startsWith("ApiFunctionServiceRole"));
    expect(apiRole).toBeDefined();
    const policies = Object.entries(template.findResources("AWS::IAM::Policy"))
      .filter(([, p]) => JSON.stringify(p.Properties.Roles).includes("ApiFunctionServiceRole"));
    for (const [, p] of policies) {
      for (const st of p.Properties.PolicyDocument.Statement) {
        const acts = ([] as string[]).concat(st.Action);
        expect(acts.every((a) => a.startsWith("logs:") || a.startsWith("xray:"))).toBe(true);
      }
    }
    expect(JSON.stringify(apiRole![1])).not.toContain("s3:");
  });

  it("retains the one-month function log group, and every log group has 30-day retention", () => {
    const groups = Object.values(template.findResources("AWS::Logs::LogGroup"));
    // the API function's group is retained; the other three are deploy-time helpers
    expect(groups.filter((g) => g.DeletionPolicy === "Retain")).toHaveLength(1);
    for (const g of groups) expect(g.Properties.RetentionInDays).toBe(30);
  });
});

describe("function url and origin access control", () => {
  const dist = () => Object.values(template.findResources("AWS::CloudFront::Distribution"))[0].Properties.DistributionConfig;
  const oacs = () => Object.entries(template.findResources("AWS::CloudFront::OriginAccessControl"));

  it("exposes exactly one Function URL and it requires AWS_IAM (no unauthenticated invocation, no CORS grant)", () => {
    template.resourceCountIs("AWS::Lambda::Url", 1);
    template.hasResourceProperties("AWS::Lambda::Url", { AuthType: "AWS_IAM", InvokeMode: "BUFFERED" });
    for (const u of Object.values(template.findResources("AWS::Lambda::Url"))) {
      expect(u.Properties.AuthType).not.toBe("NONE");
      expect(u.Properties.Cors).toBeUndefined();
    }
  });

  it("signs CloudFront-to-Lambda requests with SigV4 through a dedicated lambda-type OAC", () => {
    template.hasResourceProperties("AWS::CloudFront::OriginAccessControl", {
      OriginAccessControlConfig: Match.objectLike({ OriginAccessControlOriginType: "lambda", SigningBehavior: "always", SigningProtocol: "sigv4" }),
    });
    template.hasResourceProperties("AWS::CloudFront::OriginAccessControl", {
      OriginAccessControlConfig: Match.objectLike({ OriginAccessControlOriginType: "s3", SigningBehavior: "always" }),
    });
    expect(oacs()).toHaveLength(2); // one for S3, one for the Function URL
  });

  it("attaches that OAC to the /v1 origin, over HTTPS only with TLS 1.2, and the origin host is the Function URL", () => {
    const lambdaOac = oacs().find(([, o]) => o.Properties.OriginAccessControlConfig.OriginAccessControlOriginType === "lambda")![0];
    const api = dist().Origins.filter((o: { CustomOriginConfig?: unknown }) => o.CustomOriginConfig);
    expect(api).toHaveLength(1);
    expect(api[0].OriginAccessControlId).toEqual({ "Fn::GetAtt": [lambdaOac, "Id"] });
    expect(api[0].CustomOriginConfig).toMatchObject({ OriginProtocolPolicy: "https-only", OriginSSLProtocols: ["TLSv1.2"] });
    expect(JSON.stringify(api[0].DomainName)).toContain("FunctionUrl");
    const behaviour = dist().CacheBehaviors.find((c: { PathPattern: string }) => c.PathPattern === "/v1/*");
    expect(behaviour.TargetOriginId).toBe(api[0].Id);
  });

  it("lets only this CloudFront distribution invoke the function: two scoped statements, no public principal", () => {
    const perms = Object.values(template.findResources("AWS::Lambda::Permission")).map((p) => p.Properties);
    expect(perms).toHaveLength(2);
    expect(perms.map((p) => p.Action).sort()).toEqual(["lambda:InvokeFunction", "lambda:InvokeFunctionUrl"]);
    for (const p of perms) {
      expect(p.Principal).toBe("cloudfront.amazonaws.com");
      expect(JSON.stringify(p.SourceArn)).toMatch(/cloudfront::.*:distribution\//); // a distribution in this account
      expect(JSON.stringify(p.SourceArn)).toContain("Distribution"); // this stack's distribution
    }
    expect(perms.find((p) => p.Action === "lambda:InvokeFunction")!.InvokedViaFunctionUrl).toBe(true);
  });

  it("has no API Gateway resource, no execute-api host and no ApiEndpoint output, so there is no second public API origin", () => {
    const types = Object.values(template.toJSON().Resources).map((r) => (r as { Type: string }).Type);
    expect(types.filter((t) => t.startsWith("AWS::ApiGateway"))).toEqual([]);
    expect(json).not.toContain("execute-api");
    expect(Object.keys(template.toJSON().Outputs)).not.toContain("ApiEndpoint");
  });

  it("does not carry API Gateway throttle settings forward", () => {
    expect(json).not.toMatch(/Throttling(Rate|Burst)Limit/);
  });

  it("keeps /v1 as the public path, and the browser code and config hold no AWS signing material", () => {
    expect(dist().CacheBehaviors.map((c: { PathPattern: string }) => c.PathPattern).sort()).toEqual(["/config.json", "/v1/*"]);
    const web = path.resolve(__dirname, "../../apps/web/src");
    const walk = (d: string): string[] => fs.readdirSync(d, { withFileTypes: true })
      .flatMap((e) => (e.isDirectory() ? walk(path.join(d, e.name)) : [path.join(d, e.name)]));
    expect(fs.readFileSync(path.join(web, "api/client.ts"), "utf-8")).toContain("${BASE}/v1${path}");
    for (const f of walk(web).filter((x) => /\.tsx?$/.test(x) && !/\.test\./.test(x))) {
      const src = fs.readFileSync(f, "utf-8").replace(/\/\/.*$/gm, "");
      expect(src, f).not.toMatch(/sigv4|aws4|x-amz-|AWS_SECRET|AKIA[0-9A-Z]{16}|SecretAccessKey|@aws-sdk/i);
    }
    const cfg = Object.values(template.findResources("Custom::CDKBucketDeployment")).map((d) => JSON.stringify(d.Properties)).join("");
    // config.json is a zipped asset; the template carries one marker, the public map key read back from Amazon Location
    expect(cfg.match(/<<marker:/g)).toHaveLength(1);
    expect(cfg).toContain("ReadMapsKey");
    expect(cfg).not.toMatch(/SECRET|SIGN|CREDENTIAL/i);
  });
});

describe("cloudfront", () => {
  it("redirects to HTTPS and has a /v1 API behaviour plus an uncached /config.json", () => {
    template.hasResourceProperties("AWS::CloudFront::Distribution", Match.objectLike({
      DistributionConfig: Match.objectLike({
        DefaultRootObject: "index.html", HttpVersion: "http2and3",
        DefaultCacheBehavior: Match.objectLike({ ViewerProtocolPolicy: "redirect-to-https", Compress: true }),
        CacheBehaviors: Match.arrayWith([
          Match.objectLike({ PathPattern: "/v1/*", ViewerProtocolPolicy: "redirect-to-https", AllowedMethods: ["GET", "HEAD"] }),
          Match.objectLike({ PathPattern: "/config.json" }),
        ]),
      }),
    }));
  });

  it("never replaces API errors with the app shell: SPA fallback is a function, not distribution error pages", () => {
    const dist = Object.values(template.findResources("AWS::CloudFront::Distribution"))[0];
    expect(dist.Properties.DistributionConfig.CustomErrorResponses).toBeUndefined();
    template.resourceCountIs("AWS::CloudFront::Function", 1);
  });

  it("sends HSTS, nosniff, frame-deny, referrer policy and a CSP that allows only self and the maps host", () => {
    template.hasResourceProperties("AWS::CloudFront::ResponseHeadersPolicy", Match.objectLike({
      ResponseHeadersPolicyConfig: Match.objectLike({ SecurityHeadersConfig: Match.objectLike({
        StrictTransportSecurity: Match.objectLike({ Override: true, IncludeSubdomains: true }),
        ContentTypeOptions: { Override: true },
        FrameOptions: { FrameOption: "DENY", Override: true },
        ContentSecurityPolicy: Match.objectLike({ ContentSecurityPolicy: Match.stringLikeRegexp(
          `^default-src 'self'; script-src 'self'; .*connect-src 'self' https://maps\\.geo\\.${REGION}\\.amazonaws\\.com;.*frame-ancestors 'none'`) }),
      }) }),
    }));
    const csp = /ContentSecurityPolicy":"([^"]+)"/.exec(json)![1];
    expect(csp).not.toMatch(/script-src[^;]*('unsafe-inline'|'unsafe-eval'|\*)/);
    expect(csp).not.toMatch(/(?:^|[ ;])\*(?:[ ;]|$)/);
  });
});

describe("amazon location", () => {
  it("creates a maps-only key restricted to the site origin, in ap-south-1, with an expiry, and never outputs its value", () => {
    template.hasResourceProperties("AWS::Location::APIKey", {
      Restrictions: {
        AllowActions: ["geo-maps:GetStyleDescriptor", "geo-maps:GetTile", "geo-maps:GetSprites", "geo-maps:GetGlyphs"],
        AllowResources: [{ "Fn::Join": ["", ["arn:", { Ref: "AWS::Partition" }, `:geo-maps:${REGION}::provider/default`]] }],
        AllowReferers: [{ "Fn::Join": ["", ["https://", Match.anyValue(), "/*"]] }],
      },
      NoExpiry: false,
      ExpireTime: Match.stringLikeRegexp("^\\d{4}-\\d{2}-\\d{2}T\\d{2}:\\d{2}:\\d{2}Z$"),
    });
    const outputs = Object.keys(template.toJSON().Outputs);
    expect(outputs).toEqual(expect.arrayContaining(["SiteUrl", "ApiFunctionUrl", "SiteBucketName", "MapsApiKeyArn"]));
    expect(outputs.some((o) => /key$/i.test(o) && o !== "MapsApiKeyArn")).toBe(false);
  });

  it("supports extra referrers and writes the validated expiry in UTC", () => {
    const exp = inDays(30);
    const t = stackWith({ locationKeyExpireTime: exp, extraReferers: ["https://floodlens.example.org/*"] });
    t.hasResourceProperties("AWS::Location::APIKey", Match.objectLike({
      ExpireTime: exp.replace(/\.\d{3}Z$/, "Z"), NoExpiry: false,
      Restrictions: Match.objectLike({ AllowReferers: Match.arrayWith(["https://floodlens.example.org/*"]) }),
    }));
  });

  it("never produces a key without an expiry", () => {
    expect(json).not.toContain('"NoExpiry":true');
    for (const k of Object.values(template.findResources("AWS::Location::APIKey"))) {
      expect(k.Properties.NoExpiry).toBe(false);
      expect(k.Properties.ExpireTime).toBeDefined();
    }
  });

  it("reads the key back with a describe-only permission on that one key and hides response data", () => {
    const pol = Object.values(template.findResources("AWS::IAM::Policy"))
      .filter((p) => JSON.stringify(p.Properties.PolicyDocument).includes("geo:DescribeKey"));
    expect(pol).toHaveLength(1);
    const sts = pol[0].Properties.PolicyDocument.Statement;
    expect(sts).toHaveLength(1);
    expect(sts[0].Action).toBe("geo:DescribeKey");
    expect(JSON.stringify(sts[0].Resource)).not.toBe('"*"');
    expect(json).toContain("describeKey");
    expect(json).toContain('\\"logApiResponseData\\":false');
  });
});

describe("mandatory deployment settings", () => {
  const now = new Date("2026-10-30T00:00:00Z");

  it("rejects a missing key expiry", () => {
    expect(() => validateKeyExpiry(undefined, now)).toThrow(/required/);
    expect(() => validateKeyExpiry("", now)).toThrow(/required/);
    expect(() => new FloodLensStack(new cdk.App(), "N", { ...baseProps(), locationKeyExpireTime: undefined as unknown as string }))
      .toThrow(/locationKeyExpireTime is required/);
  });

  it("rejects malformed, impossible, expired and too-distant expiries", () => {
    for (const bad of ["tomorrow", "2026-13-40T00:00:00Z", "2026-11-30", "2026-11-30T00:00:00", "30/11/2026", "2026-02-30T00:00:00Z"]) {
      expect(() => validateKeyExpiry(bad, now), bad).toThrow(/ISO-8601|valid date/);
    }
    expect(() => validateKeyExpiry("2026-10-29T23:59:59Z", now)).toThrow(/not in the future/);
    expect(() => validateKeyExpiry("2026-10-30T00:00:00Z", now)).toThrow(/not in the future/);
    expect(() => validateKeyExpiry("2027-02-01T00:00:00Z", now)).toThrow(/more than 90 days/);
  });

  it("accepts an expiry up to exactly the maximum lifetime, and normalizes it to UTC", () => {
    expect(MAX_KEY_LIFETIME_DAYS).toBe(90);
    expect(validateKeyExpiry("2026-10-30T00:00:01Z", now)).toBe("2026-10-30T00:00:01Z");
    expect(validateKeyExpiry("2027-01-28T00:00:00Z", now)).toBe("2027-01-28T00:00:00Z"); // exactly 90 days
    expect(validateKeyExpiry("2027-01-28T05:30:00+05:30", now)).toBe("2027-01-28T00:00:00Z");
    expect(() => validateKeyExpiry("2027-01-28T00:00:01Z", now)).toThrow(/more than 90 days/);
  });

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

  it("fails the CDK app when a required setting is missing, the expiry is too far out, or a removed API Gateway setting is passed", () => {
    const run = (ctx: Record<string, string>): string => {
      try {
        execFileSync(process.execPath, [path.resolve(__dirname, "../node_modules/tsx/dist/cli.mjs"), path.resolve(__dirname, "../bin/floodlens.ts")], {
          env: { ...process.env, CDK_CONTEXT_JSON: JSON.stringify(ctx), CDK_OUTDIR: path.join(tmp, "out") },
          stdio: "pipe", cwd: path.resolve(__dirname, ".."),
        });
        return "";
      } catch (e) { return String((e as { stderr?: Buffer }).stderr ?? e); }
    };
    expect(run({ locationKeyExpireTime: inDays(30) })).toMatch(/alarmEmail is required/);
    expect(run({ alarmEmail: "ops@example.org" })).toMatch(/locationKeyExpireTime is required/);
    expect(run({ alarmEmail: "ops@example.org", locationKeyExpireTime: inDays(120) })).toMatch(/more than 90 days/);
    expect(run({ alarmEmail: "ops@example.org", locationKeyExpireTime: inDays(30), apiRateLimit: "20" })).toMatch(/no longer exists/);
  }, 60_000);
});

describe("observability and safety", () => {
  it("alarms on Lambda errors, throttles, latency and traffic spikes (no API Gateway alarm)", () => {
    const alarms = Object.values(template.findResources("AWS::CloudWatch::Alarm")).map((a) => a.Properties.MetricName);
    expect(alarms.sort()).toEqual(["Duration", "Errors", "Invocations", "Throttles"]);
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

  it("deploys content without pruning, and writes no AWS access key, secret or token anywhere", () => {
    for (const d of Object.values(template.findResources("Custom::CDKBucketDeployment"))) {
      expect(d.Properties.Prune).toBe(false);
    }
    expect(json).not.toMatch(/AKIA[0-9A-Z]{16}/);
    expect(json).not.toMatch(/ASIA[0-9A-Z]{16}/);
    expect(json).not.toMatch(/aws_secret_access_key|SecretAccessKey|aws_session_token|EARTHDATA/i);
    expect(json).not.toMatch(/v1\.public\./); // no literal browser key in the template
  });

  it("does not create destructive or billing resources", () => {
    const types = new Set(Object.values(template.toJSON().Resources).map((r) => (r as { Type: string }).Type));
    for (const t of types) expect(t).not.toMatch(/Budgets|Billing|Organizations|IAM::User$|IAM::AccessKey/);
  });

  it("fails clearly when the web build or Lambda package is missing", () => {
    expect(() => new FloodLensStack(new cdk.App(), "X", { ...baseProps(), webDistDir: path.join(tmp, "nope") })).toThrow(/npm run build/);
    expect(() => new FloodLensStack(new cdk.App(), "Y", { ...baseProps(), lambdaDir: path.join(tmp, "nope") })).toThrow(/package_lambda\.py/);
  });
});

describe("abuse limits and cost controls", () => {
  it("keys the API cache on the API's own query parameters only, so unknown parameters cannot bust it", () => {
    template.hasResourceProperties("AWS::CloudFront::CachePolicy", Match.objectLike({
      CachePolicyConfig: Match.objectLike({ ParametersInCacheKeyAndForwardedToOrigin: Match.objectLike({
        QueryStringsConfig: { QueryStringBehavior: "whitelist", QueryStrings: [...API_QUERY_PARAMS] } }) }),
    }));
  });

  it("makes reserved concurrency configurable and off by default; the API Gateway throttle options are gone", () => {
    expect(json).not.toContain("ReservedConcurrentExecutions");
    stackWith({ reservedConcurrency: 5 }).hasResourceProperties("AWS::Lambda::Function", { Runtime: "python3.13", ReservedConcurrentExecutions: 5 });
    expect("apiRateLimit" in baseProps()).toBe(false);
  });

  it("gives every deploy-time helper function an explicit one-month log group", () => {
    const groups = Object.values(template.toJSON().Resources as Record<string, { Type: string; Properties: { RetentionInDays?: number } }>)
      .filter((r) => r.Type === "AWS::Logs::LogGroup");
    expect(groups.length).toBe(4); // api function, key reader, two bucket deployments
    expect(groups.every((g) => g.Properties.RetentionInDays === 30)).toBe(true);
  });
});
