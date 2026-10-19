# Mindflow Voice Service (Pipecat + Twilio + Fish Audio)

Microsserviço independente de voz em tempo real da Mindflow.

## Stack
- **Framework:** FastAPI + WebSockets + Pipecat AI 1.12
- **Telefonia:** Twilio Media Streams (bi-direcional em tempo real)
- **STT:** Deepgram Nova-2 (`pt-BR`)
- **LLM:** OpenAI GPT-4.1 (Prompt 24 oficial da Mindflow com Prompt Caching ativo)
- **TTS:** Fish Audio Streaming WebSocket (`wss://api.fish.audio/v1/tts/live`)
- **Turn-Taking:** Calibrado contra interrupções falsas (`MinWordsUserTurnStartStrategy` + `SpeechTimeoutUserTurnStopStrategy`)

---

## 🚀 Como fazer o Deploy no Easypanel

1. No Easypanel, clique em **+ New Service** -> **App**.
2. Selecione **GitHub** e aponte para este repositório (`Sparkozzy/pipegelato`).
3. Em **Build**, o Easypanel detectará automaticamente o `Dockerfile`.
4. Em **Port**, configure `8000`.
5. Em **Domains**, adicione o domínio desejado (ex: `voice-github.bkpxmb.easypanel.host` ou `voice.mindflow.com.br`).
6. Em **Environment Variables**, insira as chaves:
   - `TWILIO_ACCOUNT_SID`
   - `TWILIO_AUTH_TOKEN`
   - `TWILIO_PHONE_NUMBER`
   - `OPENAI_API_KEY`
   - `DEEPGRAM_API_KEY`
   - `FISH_AUDIO_API_KEY`
   - `SERVER_DOMAIN` (o mesmo domínio configurado na etapa 5, sem `https://`)
7. Clique em **Deploy**.

---

## 📞 Endpoints

### `GET /health`
Verificação de saúde do serviço para o load balancer / Traefik.

### `POST /call`
Dispara uma chamada telefônica via Twilio:
```json
{
  "numero": "+5547997804224",
  "nome": "Ivan",
  "empresa": "MindFlow",
  "segmento": "Tecnologia"
}
```

### `WebSocket /ws`
Stream de áudio bi-direcional aberto automaticamente pela Twilio ao atender a chamada.
