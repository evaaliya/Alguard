---

## Amazon Bedrock — account verification blocked the AWS Builder integration

I want to describe this accurately, because the technical integration was
finished and the blocker was not in the code.

**What happened.**
1. I registered a fresh AWS account for the hackathon and enabled Bedrock.
2. Every `converse` call — across Anthropic, DeepSeek, and Amazon Nova —
   returned `ValidationException: Operation not allowed`. The error text is
   indistinguishable from a malformed request, so it took two days to
   determine it was an account-level authorization hold, not IAM, not region,
   and not model access.
3. Lifting the hold required submitting identity documents: a passport and a
   bank statement. I initially declined on principle — none of the other cloud
   accounts I use require this for a free-tier evaluation.
4. I submitted them anyway, expecting the account to unblock.
5. The account was rejected and blocked a second time, without a specific
   reason beyond the same verification wall.

**Impact on the project.**
The Bedrock integration (`mcp_server/utils/bedrock_explain.py`) is finished,
documented, and exercised through a deterministic fallback in `explain.py`.
The decision path never depends on Bedrock being reachable, so the demo runs
end to end — but the live Bedrock call could not be verified against a real
endpoint inside the hackathon window.

**Why this matters beyond one project.**
A hackathon is a low-stakes, time-boxed evaluation. Requiring government ID
and bank records before the first inference call is a barrier that student
and indie developers cannot clear in 48 hours. If that gate is intended, it
should be named in the console and in the docs before the first API call — a
distinct error code (`AccountNotAuthorizedForBedrock`) and a banner in
Billing would have saved the entire effort described above.

**What would have helped.** One paragraph at the top of the Bedrock docs:
"Some accounts require additional verification before the first call. If
`converse` returns `Operation not allowed`, check Billing → Account
Verification."