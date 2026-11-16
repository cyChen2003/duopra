# Security and data handling

Do not include credentials, recordings, conversation histories, private endpoints or personal records in public issues. Report security problems to the repository owner's private channel or GitHub private vulnerability reporting when available.

Local entrypoints bind to loopback. Docker deployments use Caddy HTTPS, password-hash authentication and exact Origin checks. One active voice session owns the model's KV cache. Access credentials, model files and runtime state are excluded from Git and container build contexts.

Calendar and task records live in the configured SQLite database. Browser identity scopes each workspace; model access requires the user's authorization. Back up the state directory or Docker state volume, including SQLite WAL files if copying a running database. Stopping with `docker compose down` preserves named volumes.

Harness API keys stay in server memory for the configuring browser and expire after inactivity. Restarting the service clears them. Enabling delegation sends the delegated task to the configured provider. `DUPLEX_HARNESS_ALLOWED_HOSTS` limits provider hosts; it does not replace network egress policy. Private tool protocol spans are excluded from direct speech conditioning; the model can use observations to formulate a public answer.

The Duopra model bundle includes executable Python. Use a trusted release; the inventory detects changed bytes but does not authenticate the publisher. Keep third-party notices and review the source and staged release before publishing. `scripts/audit_release.py` checks common secret patterns and runtime file types without printing matching values.
