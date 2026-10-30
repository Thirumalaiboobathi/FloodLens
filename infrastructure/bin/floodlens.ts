#!/usr/bin/env node
import * as cdk from "aws-cdk-lib";
import { FloodLensStack, REGION } from "../lib/floodlens-stack";

/** The only account this app may target. A mismatch aborts synth, so a wrong profile cannot deploy. */
export const EXPECTED_ACCOUNT = "675613597178";

const app = new cdk.App();
const ctx = (k: string) => app.node.tryGetContext(k) as string | undefined;
const num = (k: string): number | undefined => {
  const v = ctx(k);
  if (v === undefined) return undefined;
  const n = Number(v);
  if (!Number.isInteger(n) || n < 1) throw new Error(`context ${k} must be a positive integer, got "${v}"`);
  return n;
};

const account = process.env.CDK_DEFAULT_ACCOUNT;
if (account && account !== EXPECTED_ACCOUNT) {
  throw new Error(`Refusing to synthesize: credentials are for account ${account}, expected ${EXPECTED_ACCOUNT}.`);
}
if (process.env.CDK_DEFAULT_REGION && process.env.CDK_DEFAULT_REGION !== REGION) {
  throw new Error(`Refusing to synthesize: default region is ${process.env.CDK_DEFAULT_REGION}, expected ${REGION}.`);
}

new FloodLensStack(app, "FloodLens", {
  env: { account, region: REGION },
  description: "FloodLens flood-exposure screening: private S3, CloudFront OAC, HTTP API + Lambda, Amazon Location key",
  webDistDir: ctx("webDistDir") ?? "../apps/web/dist",
  lambdaDir: ctx("lambdaDir") ?? "build/lambda",
  alarmEmail: ctx("alarmEmail"),
  locationKeyExpireTime: ctx("locationKeyExpireTime"),
  mapStyle: ctx("mapStyle"),
  extraReferers: ctx("extraReferers")?.split(",").map((s) => s.trim()).filter(Boolean),
  apiRateLimit: num("apiRateLimit"),
  apiBurstLimit: num("apiBurstLimit"),
  reservedConcurrency: num("reservedConcurrency"),
});
