# fiware-dashboard

Flask dashboard web tier for the FIWARE smart agriculture stack.

## Run

1. Ensure Orion, QuantumLeap, Mosquitto and Keyrock are already running on the shared Docker network `fiware`.
2. Set environment variables:
   - `KEYROCK_CLIENT_ID`
   - `KEYROCK_CLIENT_SECRET`
   - `FLASK_SECRET_KEY`
   - `GRAFANA_PANEL_URL`
3. Start the dashboard:

```bash
docker compose up --build dashboard
```

Dashboard URL: `http://localhost:5000`.
