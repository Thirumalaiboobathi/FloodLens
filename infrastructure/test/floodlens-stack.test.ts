import * as fs from "node:fs";
import * as os from "node:os";
import * as path from "node:path";
import * as cdk from "aws-cdk-lib";
import { Match, Template } from "aws-cdk-lib/assertions";
import { beforeAll, describe, expect, it } from "vitest";
import { API_QUERY_PARAMS, FloodLensStack, REGION } from "../lib/floodlens-stack";

// Tiny stand-ins for the real web build and Lambda package: the stack only needs directories to exist.
let tmp: string;
let template: Template;
let json: string;
const stackWith = (extra: Partial<ConstructorParameters<typeof FloodLensStack>[2]> = {}) => {
  const app = new cdk.App();
  return Template.fromStack(new FloodLensStack(app, "T", {
    webDistDir: path.join(tmp, "web"), lambdaDir: path.join(tmp, "lambda"), ...extra }));
};

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
    template.resourceCountIs("AWS::CloudFront::OriginAccessControl", 1);
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

  it("routes only GET and HEAD under /v1 and throttles the stage", () => {
    template.hasResourceProperties("AWS::ApiGatewayV2::Route", { RouteKey: "GET /v1/{proxy+}" });
    template.hasResourceProperties("AWS::ApiGatewayV2::Route", { RouteKey: "HEAD /v1/{proxy+}" });
    const methods = Object.values(template.findResources("AWS::ApiGatewayV2::Route")).map((r) => r.Properties.RouteKey as string);
    expect(methods.every((m) => /^(GET|HEAD) \/v1\//.test(m))).toBe(true);
    template.hasResourceProperties("AWS::ApiGatewayV2::Stage", Match.objectLike({
      DefaultRouteSettings: { ThrottlingRateLimit: 50, ThrottlingBurstLimit: 100 },
      AccessLogSettings: Match.objectLike({ DestinationArn: Match.anyValue() }),
    }));
  });

  it("retains one-month log groups for the function and API access logs", () => {
    const groups = Object.values(template.findResources("AWS::Logs::LogGroup"));
    const retained = groups.filter((g) => g.DeletionPolicy === "Retain");
    expect(retained.length).toBe(2); // API function + API access logs; the other three are deploy-time helpers
    for (const g of groups) expect(g.Properties.RetentionInDays).toBe(30);
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
  it("creates a maps-only key restricted to the site origin, in ap-south-1, and never outputs its value", () => {
    template.hasResourceProperties("AWS::Location::APIKey", {
      Restrictions: {
        AllowActions: ["geo-maps:GetStyleDescriptor", "geo-maps:GetTile", "geo-maps:GetSprites", "geo-maps:GetGlyphs"],
        AllowResources: [{ "Fn::Join": ["", ["arn:", { Ref: "AWS::Partition" }, `:geo-maps:${REGION}::provider/default`]] }],
        AllowReferers: [{ "Fn::Join": ["", ["https://", Match.anyValue(), "/*"]] }],
      },
      NoExpiry: true,
    });
    const outputs = Object.keys(template.toJSON().Outputs);
    expect(outputs).toEqual(expect.arrayContaining(["SiteUrl", "ApiEndpoint", "SiteBucketName", "MapsApiKeyArn"]));
    expect(outputs.some((o) => /key$/i.test(o) && o !== "MapsApiKeyArn")).toBe(false);
  });

  it("supports an expiry and extra referrers", () => {
    const t = stackWith({ locationKeyExpireTime: "2027-10-30T00:00:00.000Z", extraReferers: ["https://floodlens.example.org/*"] });
    t.hasResourceProperties("AWS::Location::APIKey", Match.objectLike({
      ExpireTime: "2027-10-30T00:00:00.000Z", NoExpiry: false,
      Restrictions: Match.objectLike({ AllowReferers: Match.arrayWith(["https://floodlens.example.org/*"]) }),
    }));
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

describe("observability and safety", () => {
  it("alarms on Lambda errors, throttles, latency and API 5xx", () => {
    const names = Object.values(template.findResources("AWS::CloudWatch::Alarm")).map((a) => a.Properties.MetricName);
    expect(names).toEqual(expect.arrayContaining(["Errors", "Throttles", "Duration", "5xx"]));
    template.resourceCountIs("AWS::CloudWatch::Dashboard", 1);
  });

  it("subscribes an email only when one is supplied", () => {
    template.resourceCountIs("AWS::SNS::Subscription", 0);
    stackWith({ alarmEmail: "ops@example.org" }).resourceCountIs("AWS::SNS::Subscription", 1);
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
    const app = new cdk.App();
    expect(() => new FloodLensStack(app, "X", { webDistDir: path.join(tmp, "nope"), lambdaDir: path.join(tmp, "lambda") }))
      .toThrow(/npm run build/);
    expect(() => new FloodLensStack(new cdk.App(), "Y", { webDistDir: path.join(tmp, "web"), lambdaDir: path.join(tmp, "nope") }))
      .toThrow(/package_lambda\.py/);
  });
});

describe("abuse limits and cost controls", () => {
  it("keys the API cache on the API's own query parameters only, so unknown parameters cannot bust it", () => {
    template.hasResourceProperties("AWS::CloudFront::CachePolicy", Match.objectLike({
      CachePolicyConfig: Match.objectLike({ ParametersInCacheKeyAndForwardedToOrigin: Match.objectLike({
        QueryStringsConfig: { QueryStringBehavior: "whitelist", QueryStrings: [...API_QUERY_PARAMS] } }) }),
    }));
  });

  it("allows the throttle and reserved concurrency to be tuned, with safe defaults", () => {
    expect(JSON.stringify(template.toJSON())).not.toContain("ReservedConcurrentExecutions");
    const tuned = stackWith({ apiRateLimit: 10, apiBurstLimit: 20, reservedConcurrency: 5 });
    tuned.hasResourceProperties("AWS::ApiGatewayV2::Stage", {
      DefaultRouteSettings: { ThrottlingRateLimit: 10, ThrottlingBurstLimit: 20 } });
    tuned.hasResourceProperties("AWS::Lambda::Function", { Runtime: "python3.13", ReservedConcurrentExecutions: 5 });
  });

  it("gives every deploy-time helper function an explicit one-month log group", () => {
    const groups = Object.values(template.toJSON().Resources as Record<string, { Type: string; Properties: { RetentionInDays?: number } }>)
      .filter((r) => r.Type === "AWS::Logs::LogGroup");
    expect(groups.length).toBe(5); // api function, api access, key reader, two bucket deployments
    expect(groups.every((g) => g.Properties.RetentionInDays === 30)).toBe(true);
  });
});
