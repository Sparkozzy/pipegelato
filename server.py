import os
import json
import asyncio
from datetime import datetime, timezone
from dotenv import load_dotenv

# Carrega as variáveis do .env
load_dotenv()

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from twilio.rest import Client
import uvicorn

from pipecat.serializers.twilio import TwilioFrameSerializer
from pipecat.transports.websocket.fastapi import FastAPIWebsocketTransport, FastAPIWebsocketParams
from pipecat.services.deepgram.stt import DeepgramSTTService
from pipecat.services.openai.llm import OpenAILLMService
from pipecat.services.fish.tts import FishAudioTTSService
from pipecat.turns.user_start.min_words_user_turn_start_strategy import MinWordsUserTurnStartStrategy
from pipecat.turns.user_stop.speech_timeout_user_turn_stop_strategy import SpeechTimeoutUserTurnStopStrategy
from pipecat.turns.user_turn_strategies import UserTurnStrategies
from pipecat.processors.aggregators.llm_response_universal import LLMUserAggregatorParams, LLMContextAggregatorPair
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.frame_processor import FrameProcessor
from pipecat.frames.frames import LLMMessagesAppendFrame, LLMContextFrame, TranscriptionFrame
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.task import PipelineTask
from pipecat.pipeline.runner import PipelineRunner

app = FastAPI(title="Mindflow Voice API (Pipecat + Twilio)")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID")
AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN")
FROM_NUMBER = os.getenv("TWILIO_PHONE_NUMBER", "+554823980162")
SERVER_DOMAIN = os.getenv("SERVER_DOMAIN") or os.getenv("TUNNEL_DOMAIN", "voice.mindflow.com.br")

@app.get("/health")
def health_check():
    return {"status": "ok", "service": "mindflow-voice"}

twilio_client = Client(ACCOUNT_SID, AUTH_TOKEN)

def get_prompt_24(customer_name="Ivan", empresa="MindFlow", segmento="Tecnologia", numero="+5547997804224"):
    try:
        with open("prompt_24.json", "r", encoding="utf-8") as f:
            data = json.load(f)
        raw = data.get("Ligação/txt") or data.get("Prompt_Text") or ""
    except Exception as e:
        print(f"[WARN] Erro ao ler prompt_24.json: {e}")
        raw = "Você é o Caíque, SDR sênior da MindFlow. Seja breve, humano e converse sobre soluções de IA da Mindflow."

    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    date_formatted = datetime.now().strftime("%Y-%m-%d")

    prompt = (
        raw.replace("{{ customer_name }}", customer_name)
           .replace("{{customer_name}}", customer_name)
           .replace("{customer_name}", customer_name)
           .replace("{{ empresa }}", empresa)
           .replace("{{empresa}}", empresa)
           .replace("{{ segmento }}", segmento)
           .replace("{{segmento}}", segmento)
           .replace("{{ numero_do_lead }}", numero)
           .replace("{{ email }}", "ivan@mindflow.com.br")
           .replace("{{ now }}", now_iso)
           .replace("{data_atual_iso}", date_formatted)
    )
    return prompt

class CallRequest(BaseModel):
    numero: str = "+5547997804224"
    nome: str = "Ivan"
    empresa: str = "MindFlow"
    segmento: str = "Tecnologia"

@app.get("/")
def home():
    return {"status": "online", "system": "Mindflow Pipecat Voice Server", "prompt": 24, "domain": SERVER_DOMAIN}

@app.post("/call")
def make_call(req: CallRequest):
    print(f"\n[CALL] Disparando chamada para {req.numero} ({req.nome})...")
    stream_url = f"wss://{SERVER_DOMAIN}/ws"
    twiml_instruction = f"""<Response>
        <Connect>
            <Stream url="{stream_url}">
                <Parameter name="nome" value="{req.nome}" />
                <Parameter name="empresa" value="{req.empresa}" />
            </Stream>
        </Connect>
    </Response>"""
    
    call = twilio_client.calls.create(
        to=req.numero,
        from_=FROM_NUMBER,
        twiml=twiml_instruction
    )
    print(f"[OK] Chamada criada na Twilio com sucesso! SID: {call.sid}")
    return {"status": "calling", "call_sid": call.sid, "to": req.numero}

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    print("\n[WS] Conexao WebSocket recebida da Twilio!")

    stream_sid = None
    call_sid = None

    # Aguarda mensagem de início da Twilio para capturar o streamSid
    while True:
        message_text = await websocket.receive_text()
        data = json.loads(message_text)
        event = data.get("event")
        if event == "start":
            stream_sid = data["start"]["streamSid"]
            call_sid = data["start"].get("callSid")
            print(f"[STREAM] Twilio Media Stream iniciado! StreamSid: {stream_sid} | CallSid: {call_sid}")
            break
        elif event == "connected":
            print("[CONNECTED] Twilio conectada ao WebSocket...")

    # Monta o transporte Pipecat com o TwilioFrameSerializer
    serializer = TwilioFrameSerializer(
        stream_sid=stream_sid,
        call_sid=call_sid,
        account_sid=ACCOUNT_SID,
        auth_token=AUTH_TOKEN
    )

    transport = FastAPIWebsocketTransport(
        websocket=websocket,
        params=FastAPIWebsocketParams(
            audio_in_enabled=True,
            audio_out_enabled=True,
            add_wav_header=False,
            serializer=serializer
        )
    )

    # Injeta o Prompt 24 Oficial da Mindflow
    system_prompt = get_prompt_24(customer_name="Ivan", empresa="MindFlow", segmento="Tecnologia", numero="+5547997804224")
    ctx = LLMContext(messages=[{"role": "system", "content": system_prompt}])
    
    # Estratégia de turnos calibrada para evitar interrupções falsas por respiração/ruído
    strategies = UserTurnStrategies(
        start=[MinWordsUserTurnStartStrategy(min_words=2, use_interim=False)],
        stop=[SpeechTimeoutUserTurnStopStrategy(user_speech_timeout=0.6, wait_for_transcript=True)]
    )
    user_params = LLMUserAggregatorParams(user_turn_strategies=strategies)
    pair = LLMContextAggregatorPair(ctx, user_params=user_params)

    # Monitor de fala do usuário para orquestrar a abertura
    user_spoke = False

    class TurnMonitor(FrameProcessor):
        async def process_frame(self, frame, direction):
            nonlocal user_spoke
            await super().process_frame(frame, direction)
            if isinstance(frame, TranscriptionFrame) and frame.text and frame.text.strip():
                print(f"[USER TRANSCRIBED] {frame.text}")
                user_spoke = True
            await self.push_frame(frame, direction)

    monitor = TurnMonitor()

    # Serviços do Pipeline
    stt = DeepgramSTTService(
        api_key=os.getenv("DEEPGRAM_API_KEY"),
        settings=DeepgramSTTService.Settings(
            language="pt-BR",
            model="nova-2",
            smart_format=True,
            punctuate=True
        )
    )
    llm = OpenAILLMService(
        api_key=os.getenv("OPENAI_API_KEY"),
        settings=OpenAILLMService.Settings(
            model="gpt-4.1",
            temperature=0.3
        )
    )
    # Fish Audio TTS: NARRADOR DO TODO MUNDO ODEIA O CHRIS (custom_voice_066e4baeb7d31131cb8d842606)
    tts = FishAudioTTSService(
        api_key=os.getenv("FISH_AUDIO_API_KEY"),
        sample_rate=16000,
        settings=FishAudioTTSService.Settings(
            voice="4b215a58407c4def88e0892def6421b2",
            temperature=0.40,
            latency="normal"
        )
    )

    # Monta a esteira de processamento em tempo real
    pipeline = Pipeline([
        transport.input(),
        stt,
        monitor,
        pair.user(),
        llm,
        tts,
        transport.output(),
        pair.assistant()
    ])

    task = PipelineTask(pipeline)
    runner = PipelineRunner()

    # Saudação inteligente: aguarda o "Alô?" do usuário ou inicia em 1.8s se houver silêncio
    @transport.event_handler("on_client_connected")
    async def on_client_connected(transport, client):
        print("[AI] Conexao atendida! Aguardando 'Alo?' ou disparando abertura em 1.8s...")
        async def opening_fallback():
            await asyncio.sleep(1.8)
            if not user_spoke:
                print("[AI] Ivan nao falou nos primeiros 1.8s. Caíque iniciando a fala...")
                ctx.add_message({
                    "role": "user",
                    "content": "A ligação foi atendida e o lead está aguardando você falar. Diga a sua frase de abertura da Etapa 1 para o Ivan imediatamente."
                })
                await task.queue_frame(LLMContextFrame(ctx))
        asyncio.create_task(opening_fallback())

    print("[PIPECAT] Pipeline Pipecat rodando com Fish Audio e Prompt 24!")
    try:
        await runner.run(task)
    except Exception as e:
        print(f"[END] Chamada finalizada: {e}")
