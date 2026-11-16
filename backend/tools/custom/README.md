# Your own tools

Every `*.json` file in this directory is one tool that Duopra can call, next to
the 68 built in. Point `DUPLEX_CUSTOM_TOOLS` at another directory to keep them
elsewhere. Files here are local configuration and git ignores them; the
container image includes them as they are when you build it.

A tool is the registry's JSON schema format plus an `endpoint`:

| Field | | |
| :--- | :--- | :--- |
| `name` | required | lowercase words joined by dots, such as `home.lights`; must not reuse a built-in name |
| `description` | required | one sentence, the way you would explain the tool to a person |
| `parameters` | required | an object schema of the arguments |
| `returns` | required | an object schema of the result |
| `endpoint` | | `http(s)://…`; receives the arguments as a JSON `POST` and returns one JSON object |
| `read_only` | default `true` | `false` for tools that change something; they run only after you allow personal tools for the session |
| `example` | | `{"arguments": {…}, "result": {…}}`; without an endpoint the result is returned as a marked fixture |
| `domain`, `version`, `excluded`, `timeout_s` | | label in the tool list, schema version, what the tool does not do, 1–30 s (default 10) |

Schemas use `type` (`object`, `string`, `integer`, `number`, `boolean`,
`array`, `null`), `properties`, `required`, `additionalProperties`, `items`,
`enum`, `minimum`, `maximum`, `minLength`, `maxLength` and `description`. Other
keywords are rejected rather than silently ignored, and so is any file that
does not check out; the service names the file when it refuses to start.

Try the example: copy `home.lights.json.example` to `home.lights.json`, run
`python backend/tools/custom/example_endpoint.py`, restart Duopra and ask it
to turn on the kitchen lights.
