# Z.AI / GLM API — Key Verification & Model Selection

_Last verified: 2026-05-28_

## Authentication
- **Key authenticated: YES.**
- Verified via a single `GET /models` call (lists models, consumes **zero** tokens).
- HTTP status: `200 OK`.
- No fallback chat/completions call was needed (the `/models` endpoint worked).

## Working base URL
```
https://api.z.ai/api/paas/v4/
```
The models endpoint is therefore:
```
https://api.z.ai/api/paas/v4/models
```

## Available model ids
Returned in the `data` array of `/models`:

- `glm-4.5`
- `glm-4.5-air`
- `glm-4.6`
- `glm-4.7`
- `glm-5`
- `glm-5-turbo`
- `glm-5.1`

## Chosen model: `glm-5.1`
Set as `ZAI_MODEL=glm-5.1` in `.env`.

**Why:** The selection rule prefers the newest GLM family for code authoring
(`glm-5*` > `glm-4.7` > `glm-4.6` > `glm-4.5`). Among the available `glm-5*`
models:
- `glm-5.1` is the newest point release of the GLM-5 line → chosen.
- `glm-5` is the base GLM-5 (older than 5.1).
- `glm-5-turbo` is a speed/cost-optimized variant, typically lower capability —
  not ideal for the highest-quality code authoring.

So `glm-5.1` is the strongest GLM-5-family choice for code authoring.

## Example request (key never shown literally)
The key lives in `.env` as `ZAI_API_KEY` — load it into your shell first, e.g.:
```bash
export $(grep -E '^ZAI_(API_KEY|BASE_URL|MODEL)=' .env | xargs)
```

List models (no tokens consumed):
```bash
curl -s "${ZAI_BASE_URL}models" \
  -H "Authorization: Bearer $ZAI_API_KEY"
```

Minimal chat completion using the chosen model:
```bash
curl -s "${ZAI_BASE_URL}chat/completions" \
  -H "Authorization: Bearer $ZAI_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "'"$ZAI_MODEL"'",
    "messages": [{"role": "user", "content": "hi"}],
    "max_tokens": 1
  }'
```

> Never echo, print, or commit the literal value of `ZAI_API_KEY`. Always
> reference it as `$ZAI_API_KEY`.
