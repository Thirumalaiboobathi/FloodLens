# Bootstrap approval package (separate from deployment)

Written for: the project owner. 2026-10-30. **Not executed. This package asks for no approval to run anything: the evidence says bootstrap is not needed.**

## 1. Finding

The target environment is already bootstrapped. Read-only calls this phase:

| Check | Result |
|---|---|
| Target | account **675613597178**, region **ap-south-1** (`aws sts get-caller-identity` matches; region from `~/.aws/config`) |
| Stack | `CDKToolkit` in `ap-south-1`: `CREATE_COMPLETE`, created 2026-08-20 (not updated since) |
| Qualifier | `hnb659fds` (the CDK default, which this app uses) |
| Bootstrap version | **32** (`BootstrapVersion` output; SSM `/cdk-bootstrap/hnb659fds/version` = 32). The synthesized app requires versions 6 and 8, so 32 satisfies it |
| Resources in the stack | `StagingBucket` (+ policy), `ContainerAssetsRepository` (ECR), `FilePublishingRole`, `ImagePublishingRole`, `DeploymentActionRole`, `LookupRole`, `CloudFormationExecutionRole`, SSM parameter |
| Staging bucket | `cdk-hnb659fds-assets-675613597178-ap-south-1` (exists; also used by the account's other CDK projects) |

## 2. Recommendation

**Do not run `cdk bootstrap`.** It is unnecessary, and the toolkit stack is shared with other projects in this account (ShopFlow, AwsDeployDoctor); re-running it would update that shared stack. If any check above later fails (for example the version parameter is missing or the stack is in a failed state), come back to this package.

## 3. If bootstrap were ever needed (for completeness; not requested)

| Item | Detail |
|---|---|
| Why it is required | CDK needs a staging bucket, publishing roles and a CloudFormation execution role to upload the Lambda zip (about 16 MiB), the web assets and the template |
| Exact command | `cd infrastructure && npx cdk bootstrap aws://675613597178/ap-south-1` |
| Resources it would create or update | the `CDKToolkit` stack resources listed in section 1 (here: an update of existing ones) |
| Permissions required | CloudFormation, IAM role creation, S3, ECR, SSM in the account. The configured principal (`AI-agent`, AdministratorAccess) has them |
| Cost | negligible: a few cents per month for the staging bucket and an empty ECR repository (no lifecycle cost beyond stored assets; ECR is unused by FloodLens) |
| Risk | modifies a toolkit stack that other projects depend on; a failed update could affect their deployments |

## 4. Approval

* Bootstrap approval requested: **none** (not required).
* Owner decision needed only if you disagree with the finding or want a dedicated bootstrap qualifier for FloodLens (not recommended: it needs code changes and a new staging bucket).
