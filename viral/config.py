from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
CACHE = ROOT / "cache"
OUTPUT = ROOT / "output"
ASSETS = ROOT / "assets"
FONTS = ASSETS / "fonts"
for _p in (CACHE, OUTPUT, FONTS, CACHE / "tts", CACHE / "images", CACHE / "rss"):
    _p.mkdir(parents=True, exist_ok=True)

# Perfis de renderizacao. "poc" e rapido para testar na maquina fraca; "final" e o
# que vai para o TikTok (1080x1920 e o formato nativo vertical).
QUALITY = {
    "poc": dict(width=720, height=1280, fps=30, crf=23, preset="veryfast"),
    "final": dict(width=1080, height=1920, fps=30, crf=20, preset="medium"),
}

SR = 44100  # taxa de amostragem de todo o audio

# Voz padrao. OpenAI (gpt-4o-mini-tts): ash (energetica, padrao), verse, echo, cedar, onyx (grave).
# Amostras em output/vozes/<voz>.mp3. Troque com --voice NOME. Edge gratuito (sem chave): pt-BR-AntonioNeural.
VOICE = "cedar"
VOICE_ALT = "pt-BR-FranciscaNeural"
VOICE_RATE = "+0%"  # ritmo do Edge; as pausas longas sao apertadas depois pelo editor
# Velocidade da voz OpenAI (1.0 = normal). O tema pode subir um pouco (chave "speed" em THEMES).
# Acima de ~1.1 soa esticado: o ritmo vem principalmente das instrucoes em viral/voice/tts.py.
VOICE_SPEED = 1.0
LANG = "pt"

# Duracao alvo do video. TikTok premia retencao: 30-50 s e a faixa que mais
# completa reproducao para conteudo falado.
TARGET_SECONDS = (28, 52)
MAX_SCENE_SECONDS = 4.6  # nenhum plano fica parado mais que isso (uma imagem por frase: 3 a 5 s)
VOICE_PITCH = "+0Hz"
PAUSE_BETWEEN_BEATS = 0.28
PAUSE_AFTER_HOOK = 0.45

# Trilhas sintetizadas (viral/audio/synth.py): andamento e tonica de cada estilo,
# usados quando o assunto troca a trilha padrao do tema (viral/audio/mood.py).
# Musica propria em assets/music/<estilo>/ passa na frente da sintese.
MUSIC_STYLES = {
    "synthwave": dict(bpm=124, root=110.0),    # noticia: energico, neon, gancho de lead
    "lofi": dict(bpm=86, root=130.81),         # curiosidade: calmo, curioso
    "cinematic": dict(bpm=72, root=87.31),     # neste dia: cinematografico, calmo
    "tense": dict(bpm=104, root=98.0),         # tenso (legado; teoria agora usa "arp")
    "arp": dict(bpm=104, root=130.81),         # alegre, curioso: arpejo em tom maior
    "suspense": dict(bpm=92, root=98.0),       # assunto grave: tom menor, bordao grave, tensao que cresce
    "urgent": dict(bpm=122, root=110.0),       # noticia quente: pulso em semicolcheias, bumbo forte
}
# Trilha sob a voz: alvos em dB do volume medio da trilha (RMS) em relacao a fala (RMS), usados
# por audio/mix.py (Mixer.add_music). MUSIC_GAIN vale em pausa de 0,6 s ou mais; MUSIC_DUCK,
# enquanto a voz fala. Pausa mais curta mantem a trilha em MUSIC_DUCK.
# 13/09/2026: trilha subiu (era -12 e -20). Com a voz no centro e a musica aberta nos lados, -15 dB sob
# a fala fica presente sem cobrir a voz; em pausa a musica respira a -9 dB.
MUSIC_GAIN = -9.0
MUSIC_DUCK = -15.0
# 13/09/2026: roteiro em tom engracado com palavrao censurado por piii (viral/script/zoeira.py). False volta ao tom serio.
ZOEIRA = True

# Estilo por tema. Cada tema muda paleta, trilha, transicoes e jeito do texto
# entrar. E daqui que a "edicao dinamica de acordo com o tema" sai. A trilha
# aqui e o padrao do tema; o assunto pode trocar (viral/audio/mood.py).
THEMES = {
    "tech_news": dict(
        label="NOTICIA TECH",
        bg=(10, 12, 20), surface=(20, 24, 36), text=(245, 247, 250),
        accent=(0, 229, 255), accent2=(255, 214, 0),
        music=dict(style="synthwave", bpm=124, root=110.0),
        transitions=["glitch", "whip", "slide_left", "zoom_in", "flash"],
        punch_style="pop", grain=0.0, kenburns=(1.0, 1.16), procedural="grid", speed=1.05,
        hashtags=["tecnologia", "tech", "noticias", "ti", "inovacao"],
    ),
    "story": dict(
        label="ACONTECEU",
        bg=(18, 8, 10), surface=(34, 16, 20), text=(250, 245, 245),
        accent=(255, 69, 58), accent2=(255, 214, 0),
        music=dict(style="suspense", bpm=92, root=98.0),
        transitions=["glitch", "flash", "whip", "zoom_in"],
        punch_style="glitch", grain=0.05, kenburns=(1.0, 1.18), procedural="noise", speed=1.05,
        hashtags=["historia", "tecnologia", "ti", "curiosidades", "aconteceu"],
    ),
    "curiosity": dict(
        label="CURIOSIDADE DE T.I.",
        bg=(22, 14, 40), surface=(38, 26, 66), text=(250, 248, 255),
        accent=(255, 204, 0), accent2=(0, 229, 255),
        music=dict(style="lofi", bpm=86, root=130.81),
        transitions=["crossfade", "slide_up", "zoom_in", "wipe"],
        punch_style="typewriter", grain=0.0, kenburns=(1.0, 1.12), procedural="bokeh", rate="-4%",
        hashtags=["curiosidades", "ti", "programacao", "tecnologia", "vocesabia"],
    ),
    "history": dict(
        label="NESTE DIA",
        bg=(24, 18, 14), surface=(44, 34, 26), text=(250, 242, 228),
        accent=(232, 184, 96), accent2=(255, 255, 255),
        music=dict(style="cinematic", bpm=72, root=87.31),
        transitions=["crossfade", "wipe", "zoom_in"],
        punch_style="stamp", grain=0.09, kenburns=(1.0, 1.09), procedural="film", rate="-6%",
        hashtags=["historia", "tecnologia", "nestedia", "ti", "curiosidades"],
    ),
    "theory": dict(
        label="MITO OU VERDADE",
        bg=(14, 10, 24), surface=(30, 22, 48), text=(248, 246, 255),
        accent=(255, 149, 0), accent2=(0, 229, 255),
        music=dict(style="arp", bpm=104, root=130.81),
        transitions=["crossfade", "whip", "zoom_in", "flash"],
        punch_style="pop", grain=0.03, kenburns=(1.0, 1.14), procedural="noise",
        hashtags=["teoria", "tecnologia", "mito", "verdade", "ti"],
    ),
    "prediction": dict(
        label="PREVISAO COM DADOS",
        bg=(4, 12, 8), surface=(10, 26, 18), text=(240, 255, 244),
        accent=(57, 255, 20), accent2=(255, 214, 0),
        music=dict(style="arp", bpm=100, root=110.0),
        transitions=["slide_left", "zoom_in", "glitch", "flash"],
        punch_style="pop", grain=0.0, kenburns=(1.0, 1.14), procedural="scanlines",
        hashtags=["futuro", "dados", "tecnologia", "previsao", "ti"],
    ),
}

BASE_HASHTAGS = ["fyp", "foryou", "viral", "tiktokbrasil"]

USER_AGENT = "UsinaViral/0.1 (+https://github.com/; projeto pessoal de estudo)"
