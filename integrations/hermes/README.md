# Hermes transcript reader snapshot

These files version the reader already installed in the existing Hostinger Hermes
runtime. They are not another runtime or an HTTP client: `meeting_io.py` uses the
existing `AgencyClient` connection and its scoped GET transport hook.

Runtime destination: `/data/workspace/hermes-agency-runtime/hermes/agency_adapter/`.
Deploy the Core route before using `MeetingReads.transcript(meeting_id)`.
The read requires explicit canonical `client_id`, authenticated Agency API access,
matching Fireflies provider identity and exact UTF-8 SHA-256/version binding.
No writes, provider calls, sends, credentials or environment files are included.

The contract checksum must match `meeting-contract.json`. Pending local-deploy
metadata was replaced with the matching Core source contract marker; wire schemas
and reader behavior are unchanged. No changes to processing or replay are included.
