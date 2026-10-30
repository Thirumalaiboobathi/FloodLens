# Deployment IAM requirements and least-privilege recommendation

> **Superseded in part by Phase 13 (2026-10-30).** The deployment target changed from CloudFront + S3 + Lambda Function URL to
> **AWS Amplify Hosting + API Gateway HTTP API + Lambda** (stack `FloodLensAmplify`), after the first CloudFront deployment failed on the
> account's cache-policy query-string quota (see `reports/releases/2026-10-30-rc2/ROLLBACK-INVESTIGATION.md`). Statements below about
> CloudFront, the Function URL, OAC, `config.json`, the 4-alarm set, reserved concurrency and the `FloodLens` stack describe the old design.
> Current procedure: [AMPLIFY_MIGRATION.md](AMPLIFY_MIGRATION.md). Kept for the audit trail; its commands were never run against AWS.


Written for: the project owner. 2026-10-30. **No IAM user, role or policy was created or changed.** The JSON below is a proposal. Labels: **[V]** read-only verified · **[S]** from the synthesized template · **[A]** assumption · **[U]** unknown.

## 1. Current situation [V]

| Item | Finding |
|---|---|
| Deploying principal | IAM user `AI-agent`, long-term access key, `AdministratorAccess` among its policies |
| CDK bootstrap (v32, qualifier `hnb659fds`) | Roles: `cdk-hnb659fds-deploy-role-…`, `…-file-publishing-role-…`, `…-lookup-role-…`, `…-cfn-exec-role-…` (and an image role, unused here) |
| Who may assume the bootstrap roles | The **account root** is trusted (so any principal in the account that is allowed `sts:AssumeRole` on them). The same roles serve every CDK project in the account (ShopFlow, AwsDeployDoctor) |
| `cfn-exec-role` | **`AdministratorAccess`.** CloudFormation executes every stack, FloodLens included, as an administrator |
| `deploy-role` | CloudFormation change-set/stack actions, staging-bucket access, SSM version read, and `iam:PassRole` on **only** the cfn-exec role |
| `lookup-role` | `ReadOnlyAccess` |

**Consequence:** replacing the human user's permissions makes the *person at the keyboard* least-privilege, but the *stack deployment itself* still runs with administrator rights through the shared `cfn-exec-role`. A narrower execution role would need either a re-bootstrap with `--cloudformation-execution-policies` (rejected: shared stack, owner decision 1) or `cdk deploy --role-arn <role>`; because the deploy role may pass only the cfn-exec role, I expect `--role-arn` with another role to be refused [U, untested, not attempted]. State this residual risk plainly when approving: **FloodLens's stack is created by an admin-powered role, but its definition is a reviewable template (37 resources, one `Resource: *` statement for CloudFront invalidation) [S].**

## 2. Recommendation

1. **Do not keep long-term AdministratorAccess on the deploying identity.** Create a dedicated principal (a role that you assume with MFA, or short-lived credentials from IAM Identity Center) used only for FloodLens deployments. Remove or deactivate the long-term access key of `AI-agent` after the deployment, or replace its policies with the read-only set in 3.3. [A: choose what fits how you sign in.]
2. Give it only: `sts:AssumeRole` on the three bootstrap roles it needs, plus the narrow read-only verification calls (section 3).
3. Run owner-only operations (budgets, quota requests, key revocation) as a **separate** break-glass identity or in the console, not from the deploy identity.
4. Keep `cdk bootstrap` out of the deployer's permissions: it is not needed (v32 exists).

## 3. Proposed policies (not created)

### 3.1 Deployer: assume the CDK bootstrap roles only

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {"Sid": "AssumeCdkBootstrapRoles", "Effect": "Allow", "Action": "sts:AssumeRole",
     "Resource": [
       "arn:aws:iam::675613597178:role/cdk-hnb659fds-deploy-role-675613597178-ap-south-1",
       "arn:aws:iam::675613597178:role/cdk-hnb659fds-file-publishing-role-675613597178-ap-south-1",
       "arn:aws:iam::675613597178:role/cdk-hnb659fds-lookup-role-675613597178-ap-south-1"]},
    {"Sid": "Identity", "Effect": "Allow", "Action": "sts:GetCallerIdentity", "Resource": "*"},
    {"Sid": "SeeTheStack", "Effect": "Allow",
     "Action": ["cloudformation:DescribeStacks", "cloudformation:DescribeStackEvents", "cloudformation:DescribeStackResources", "cloudformation:ListStackResources", "cloudformation:GetTemplate"],
     "Resource": "arn:aws:cloudformation:ap-south-1:675613597178:stack/FloodLens/*"}
  ]
}
```

Not included on purpose: `cdk-…-image-publishing-role` (no containers), `cdk-…-cfn-exec-role` (CloudFormation uses it, not the person), IAM, S3 or Lambda write actions. Add `cloudformation:DeleteStack`-class rights only if the owner wants destroy capability (not recommended).

### 3.2 What the stack's own actions need (informational: for a future narrowed execution role)

What `cfn-exec-role` must be able to do for this stack [S]. Resource scoping is by the `FloodLens` stack-name prefix wherever a name is generated; exact ARNs are known only after deploy [U].

| Area | Actions | Scope |
|---|---|---|
| CloudFormation | the stack's own operations (`Create/Update/Delete/DescribeStack*`, `GetTemplateSummary`) | stack `FloodLens` |
| Lambda | `CreateFunction`, `UpdateFunctionCode/Configuration`, `DeleteFunction`, `GetFunction*`, `AddPermission`, `RemovePermission`, `GetPolicy`, `TagResource`, `PutFunctionConcurrency`/`DeleteFunctionConcurrency` (if reserved), layers `PublishLayerVersion`/`GetLayerVersion` for the CDK helper CLI layer | functions named `FloodLens-*` and the helper functions |
| Lambda Function URLs | `CreateFunctionUrlConfig`, `UpdateFunctionUrlConfig`, `DeleteFunctionUrlConfig`, `GetFunctionUrlConfig`, and `lambda:InvokeFunctionUrl` / `lambda:InvokeFunction` as permission-statement actions (managed through `AddPermission`) | the API function |
| CloudFront | `CreateDistribution`, `UpdateDistribution`, `DeleteDistribution`, `GetDistribution*`, `TagResource`; `CreateOriginAccessControl`/`Update`/`Delete`/`Get` (two OACs); `CreateCachePolicy`, `CreateResponseHeadersPolicy`, `CreateFunction`/`PublishFunction`/`DeleteFunction` (viewer-request function) and matching update/get/delete; `CreateInvalidation`/`GetInvalidation` | account-level (CloudFront resources are global; most actions do not support resource scoping) |
| S3 | create/configure/delete the site bucket (`CreateBucket`, `PutBucketPolicy`, `PutEncryptionConfiguration`, `PutBucketVersioning`, `PutBucketPublicAccessBlock`, `PutBucketTagging`, `DeleteBucketPolicy`), object read/write/delete for the deployment custom resource; read of the CDK assets bucket (`cdk-hnb659fds-assets-675613597178-ap-south-1`) | the site bucket (name generated) and the assets bucket |
| Amazon Location | `geo:CreateKey`, `geo:UpdateKey`, `geo:DeleteKey`, `geo:DescribeKey`, `geo:TagResource`, `geo:ListKeys` | the one key (name derived from the stack id) |
| IAM | `CreateRole`, `DeleteRole`, `GetRole`, `PutRolePolicy`, `DeleteRolePolicy`, `AttachRolePolicy`, `DetachRolePolicy`, `PassRole`, `TagRole` for the 3 roles the stack creates | role names starting with `FloodLens-` (generated) |
| SNS | `CreateTopic`, `DeleteTopic`, `SetTopicAttributes`, `Subscribe`, `Unsubscribe`, `GetTopicAttributes`, `TagResource` | topic `FloodLens-*` |
| CloudWatch | `PutMetricAlarm`, `DeleteAlarms`, `DescribeAlarms`, `PutDashboard`, `DeleteDashboards`; Logs `CreateLogGroup`, `PutRetentionPolicy`, `DeleteLogGroup`, `TagResource`, `DescribeLogGroups` | alarms/dashboard/log groups named `FloodLens*` or `/aws/lambda/FloodLens*` |
| CDK bootstrap assets | `s3:GetObject*`/`ListBucket` on the assets bucket (read by the execution role and by the bucket-deployment helper); the **file-publishing role** does the upload with `s3:PutObject`; SSM `GetParameter` on `/cdk-bootstrap/hnb659fds/version` | assets bucket, that parameter |

The custom resources (`ReadMapsKey` reads `geo:DescribeKey`; the bucket deployment writes to the site bucket and invalidates CloudFront) get their permissions from roles the *stack* creates, which is why the IAM actions above are needed [S].

### 3.3 Post-deployment verification and operations (read-only subset)

`lambda:GetFunction`, `GetFunctionConfiguration`, `GetFunctionUrlConfig`, `GetPolicy`, `GetFunctionConcurrency`, `GetAccountSettings`; `cloudwatch:DescribeAlarms`, `GetMetricData`, `ListMetrics`; `logs:FilterLogEvents`, `GetLogEvents`, `DescribeLogGroups`; `sns:ListSubscriptionsByTopic`, `GetTopicAttributes`; `cloudfront:GetDistribution`, `GetDistributionConfig`; `geo:ListKeys`, `DescribeKey`; `servicequotas:GetServiceQuota`, `ListRequestedServiceQuotaChangeHistoryByQuota`; `budgets:ViewBudget`; `ce:Get*`. Prefer the AWS-managed `ReadOnlyAccess` for this role if exact scoping is not worth the effort.

### 3.4 Owner-only (do not give to the deployer)

`budgets:CreateBudget`/`ModifyBudget`, `servicequotas:RequestServiceQuotaIncrease`, `geo:UpdateKey` (emergency revocation), `ce:CreateAnomalySubscription`, `s3:DeleteObject` on the CDK assets bucket (the I-1 cleanup), and any IAM administration.

## 4. Risks that remain whatever the owner chooses

| Risk | Severity | Note |
|---|---|---|
| Stack executes as AdministratorAccess via the shared `cfn-exec-role` | MEDIUM | owner decision 1 rules out re-bootstrapping; mitigated by reviewing the template and by the small, fixed resource list |
| Bootstrap roles trust the whole account root | LOW | any principal with `sts:AssumeRole` permission can use them; keep admin users few and MFA-protected |
| Long-term access keys | MEDIUM | prefer short-lived credentials |
| Wildcard `cloudfront:CreateInvalidation` in the bucket-deployment policy | LOW | CDK default; the invalidation action is harmless but is not scoped to the distribution |

## 5. Verify the setup before use (read-only)

```bash
aws sts get-caller-identity
aws iam list-attached-user-policies --user-name AI-agent
aws iam list-access-keys --user-name AI-agent --query "AccessKeyMetadata[].[AccessKeyId,Status,CreateDate]" --output text   # print the key IDs, never secrets
```
