# Legacy Demo Assets

The following files contain old Air University Multan Campus demo/test assumptions and are not the production multi-organization runtime path:

- `assistant.py`
- `evaluate_metrics.py`
- `test_intents.py`
- `test_pipeline.py`
- `_audit_queries.py`
- `data/responses/intents.json`
- `data/responses/enhanced_intents.json`
- `IT_Proposal.txt`

Production VOX should use:

- `serve.py` / `wsgi.py`
- `app.py`
- `organizations/<org_id>/profile.json`
- `organizations/<org_id>/intents.json`
- `organizations/<org_id>/documents`
- `organizations/<org_id>/vector_index`

The bundled evaluation route in `app.py` is guarded so it only runs for the default legacy organization.
