# Healthcare Phase One Scaffold

This project now includes scaffolding for the Phase One requirements from the healthcare AI agent plan.

## Current Architecture

The system still uses one main `KnowledgeAgent`, but it now exposes healthcare-specific capabilities that map to the Phase One tool list:

| Phase One Requirement | Scaffolded Capability | Implementation |
|---|---|---|
| Document search | `document_search` plus existing `rag_search` | `backend/app/healthcare_tools.py`, `backend/app/retrieval.py` |
| Policy search | `policy_search` | Filters retrieved context toward clinical/admin policies, SOPs, pathways, guidelines |
| Catalogue search | `catalogue_search` | Uses S3 document manifest metadata for services, owners, systems, directories |
| Calendar / rota | `calendar_rota_lookup` | Reads approved CSV schedule/rota sources from S3 |
| Table / formulary lookup | `formulary_table_lookup` | Uses structured CSV rows for medicines, approval rules, codes, structured facts |
| Safety guard | `safety_guard` | Flags PHI, urgent clinical risk, patient-specific requests, missing citations |
| Role-based filtering | `HealthcareAccessControl` | Filters documents and retrieval hits using `allowed_roles` metadata |
| PHI redaction | `PHIRedactor` | Redacts common identifiers before prompt/audit use |
| Audit logging | `HealthcareAuditLogger` | Emits JSON audit events with user, role, tools, sources, trace, safety, tokens |
| Source governance | `SourceGovernance` metadata | Owner, version, effective date, review date, approval status, sensitivity, domain |
| DOCX ingestion | `parse_document()` support | `backend/app/ingest.py` supports `.docx` text/table extraction |
| Healthcare evals | `healthcare_golden_dataset.csv` | Golden question scaffold for policy, rota, catalogue, formulary, safety cases |

## Healthcare User Roles

The simple login model now supports optional user profiles in the app secret:

```json
{
  "session_secret": "replace-with-long-random-value",
  "auth_users": {
    "admin": "pbkdf2_sha256$200000$salt_hex$hash_hex"
  },
  "user_profiles": {
    "admin": {
      "roles": ["admin", "doctor"],
      "departments": ["clinical_governance"]
    }
  }
}
```

Roles are added to the signed token and converted into `HealthcareUserContext`.

## Source Governance Metadata

Ingestion now adds default governance fields:

```json
{
  "owner": "unknown",
  "version": "unknown",
  "effective_date": "unknown",
  "review_date": "unknown",
  "approval_status": "unknown",
  "sensitivity": "internal",
  "domain": "clinical_policy",
  "document_type": "policy",
  "allowed_roles": ["doctor", "nurse", "clinical_governance", "admin"]
}
```

The scaffold infers a starting `domain`, `document_type`, and `allowed_roles` from the S3 object key. In production, these should come from a reviewed document registry or metadata sidecar.

## What Is Still A Scaffold

These additions create the code paths and contracts, but they are not a complete healthcare production system yet.

Remaining hardening work:

- Replace inferred metadata with approved source governance records.
- Add real SSO/Cognito or identity-provider integration.
- Add true document-level authorization in OpenSearch queries, not only post-retrieval filtering.
- Store audit logs in a durable healthcare audit sink.
- Add formal PHI redaction evaluation.
- Add clinical safety review and escalation policy configuration.
- Add real calendar/rota integration instead of CSV scaffolding.
- Add formulary schema validation and pharmacist-reviewed source data.
- Expand RAGAS golden data with approved healthcare answers and expected citations.

