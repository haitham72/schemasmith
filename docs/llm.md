# A local model for the designer

The normal app works with `LLM_MODEL` empty. The designer needs `SCHEMA_EDIT=1` and either
a user-supplied OpenAI key (entered on the prompt screen, sent as `X-OpenAI-Key`, fixed to
`api.openai.com`) or a server-side OpenAI-compatible `/v1/chat/completions` endpoint, used by
admins. Guests need `GUEST_DESIGNER=1` and always bring their own key. Model
output becomes a proposed JSON spec. Generation, planning and applying are
separate actions; inspect the plan and confirm before anything changes.

## Ollama in Compose

Set these values in `.env`:

```dotenv
SCHEMA_EDIT=1
LLM_BASE_URL=http://ollama:11434/v1
LLM_MODEL=qwen3:8b
```

Start the optional service, download a local model, then recreate web:

```sh
docker compose --profile llm up -d
docker compose exec ollama ollama pull qwen3:8b
docker compose up -d --force-recreate web
```

`qwen3:8b` is a starting candidate, not a model-quality certification for this app.
`llama3.1:8b` is another small-model candidate. Choose according to available
memory, latency and the model's license; the three-use-case quality check remains
unverified. Check the current [Qwen3 8B](https://ollama.com/library/qwen3:8b) and
[Llama 3.1 8B](https://ollama.com/library/llama3.1:8b) listings before downloading.
Ollama documents its [OpenAI-compatible API](https://docs.ollama.com/api/openai-compatibility).

The CPU-only Compose profile works without a GPU configuration but a full prompt
includes both examples and the JSON Schema. Allow enough model context and memory;
a 120-second timeout can be reached on slower hardware. Select local model tags
and pre-download weights for offline operation.

## LM Studio on the host

Download/load a model in LM Studio and start its local server. Use the model ID
reported by that server. When running Python directly use
`LLM_BASE_URL=http://localhost:1234/v1`. From the web container use
`http://host.docker.internal:1234/v1`; Compose supplies a host-gateway mapping.
The host server must listen on an interface reachable from the container, with
firewall access restricted to that trusted network. Enable and configure
`LLM_API_KEY` if authentication is enabled on the server. See LM Studio's
[compatibility endpoint reference](https://lmstudio.ai/docs/developer/openai-compat).

## Any compatible endpoint

Set the URL, exact model ID and optional API key, then recreate web. The client
requests JSON-object output, falling back only if a 400/422 response explicitly
rejects that format. Fenced/prose-wrapped JSON is parsed as data. The full spec is
validated with the closed schema. Structured validation errors feed the repair
loop; all provider calls, including fallback, share a budget of three requests.
After exhaustion the UI receives readable errors with JSON paths.

The system prompt contains the closed field type list, validation/migration rules,
generated JSON Schema and both bundled examples. Use cases are at most 4,000
characters; specs are at most 64 KiB. No provider SDK or cloud account is required.

## Privacy and verification

Use-case text, the current spec when revising, schema and bundled examples go only
to the endpoint you configure. Existing record rows are not automatically included.
If you paste confidential details into a use case, those details are sent to that
endpoint. A remote provider can receive and retain them according to its policy.
Local model runtimes may have their own downloads, telemetry or cloud options;
configure those independently before isolating the network.

Provider redirects and environment proxy discovery are disabled. API keys, raw
provider bodies, prompts and raw responses are not logged by the designer module.
Stub-server tests cover protocol and repair behavior; they do not establish real
model quality; local Ollama/LM Studio model quality is unverified.
