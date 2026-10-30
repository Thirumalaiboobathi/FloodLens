#!/usr/bin/env node
import * as cdk from "aws-cdk-lib";
import { FloodLensStack, REGION, validateAlarmEmail, validateFrontendOrigins, validateMapsApiKeyArn } from "../lib/floodlens-stack";

/** The only account this app may target. A mismatch aborts synth, so a wrong profile cannot deploy. */
export const EXPECTED_ACCOUNT = "675613597178";

/**
 * The backend stack's identity. It is deliberately not "FloodLens": that earlier stack is in ROLLBACK_COMPLETE and CloudFormation
 * cannot update it. A distinct name avoids touching it; its eventual cleanup is documented in docs/deployment/AMPLIFY_MIGRATION.md.
 */
export const STACK_NAME = "FloodLensAmplify";

const app = new cdk.App();
const ctx = (k: string) => app.node.tryGetContext(k) as string | undefined;
const num = (k: string): number | undefined => {
  const v = ctx(k);
  if (v === undefined) return undefined;
  const n = Number(v);
  if (!Number.isInteger(n) || n < 1) throw new Error(`context ${k} must be a positive integer, got "${v}"`);
  return n;
};

// The owner chose the shared account concurrency pool. A reservation must not appear silently.
if (ctx("reservedConcurrency") !== undefined) {
  throw new Error("context reservedConcurrency is not supported: the owner accepted unreserved Lambda concurrency. " +
    "Changing that is a decision for the owner and a change to the stack, not a deploy-time flag.");
}

const account = process.env.CDK_DEFAULT_ACCOUNT;
if (account && account !== EXPECTED_ACCOUNT) {
  throw new Error(`Refusing to synthesize: credentials are for account ${account}, expected ${EXPECTED_ACCOUNT}.`);
}
if (process.env.CDK_DEFAULT_REGION && process.env.CDK_DEFAULT_REGION !== REGION) {
  throw new Error(`Refusing to synthesize: default region is ${process.env.CDK_DEFAULT_REGION}, expected ${REGION}.`);
}

// Required for every synth: alarms need a recipient, CORS must name the real Amplify origin(s), and the existing
// (console-created) map key's ARN is recorded in an output. The key itself is not managed here.
const alarmEmail = validateAlarmEmail(ctx("alarmEmail"));
const mapsApiKeyArn = validateMapsApiKeyArn(ctx("mapsApiKeyArn")).arn;
const frontendOrigins = validateFrontendOrigins(ctx("frontendOrigins")?.split(",").map((s) => s.trim()).filter(Boolean));

new FloodLensStack(app, STACK_NAME, {
  env: { account, region: REGION },
  description: "FloodLens backend: API Gateway HTTP API, Lambda (FastAPI, ARM64), CloudWatch alarms. Frontend is hosted by AWS Amplify.",
  lambdaDir: ctx("lambdaDir") ?? "build/lambda",
  alarmEmail,
  mapsApiKeyArn,
  frontendOrigins,
  allowLocalDevCors: ctx("allowLocalDevCors") === "true",
  mapStyle: ctx("mapStyle"),
  apiRateLimit: num("apiRateLimit"),
  apiBurstLimit: num("apiBurstLimit"),
  invocationAlarmThreshold: num("invocationAlarmThreshold"),
});
