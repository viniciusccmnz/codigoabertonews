"""Escolha do clima da trilha pelo conteudo do roteiro.

O tema da um padrao (noticia = synthwave, incidente = suspense...), mas uma
noticia sobre IA tirando emprego ou vazamento de dados pede suspense, e um
anuncio de preco ou lancamento pede urgencia. Regras puras, sem rede, para
o pipeline e os testes. `mood_for` devolve dict(style, bpm, root) no mesmo
formato de THEMES[tema]["music"].
"""
from __future__ import annotations

import re
import unicodedata

MOODS = {
    "suspense": dict(style="suspense", bpm=92, root=98.0),    # thriller: IA preocupante, vazamento, ataque
    "urgent": dict(style="urgent", bpm=122, root=110.0),      # noticia quente: anuncio, preco, hoje
    "tense": dict(style="tense", bpm=104, root=98.0),         # incidente em andamento
}

# Palavras (pt e en, sem acento) que puxam o clima. Cada acerto soma 1 ponto.
SUSPENSE_WORDS = (
    "vazamento vazou vazaram hacker hackers hackeado ataque atacou invasao invadiu ransomware malware "
    "espionagem espiona vigilancia privacidade rastreia rastreamento demissao demissoes demite demitiu "
    "demitidos corta cortou cortes desemprego emprego empregos substitui substituir substituindo proibe "
    "proibiu proibicao proibido banido banimento guerra militar militares arma armas drone drones deepfake "
    "golpe golpes fraude risco riscos perigo perigoso ameaca ameacas alerta crise falha falhou pane apagao "
    "morte morreu processo processa processou multa multou investigacao investiga censura controle "
    "manipula manipulacao vicio viciante consciencia superinteligencia extincao apocalipse descontrole "
    "escondeu esconde segredo secreto sigilo denuncia denunciou acusa acusacao "
    "breach leak leaked hack hacked attack surveillance privacy tracking layoffs layoff fired jobs ban "
    "banned war weapon weapons scam fraud risk danger dangerous threat warning crisis outage lawsuit sued "
    "fine investigation censorship manipulation addictive extinction rogue secret hidden whistleblower"
).split()

URGENT_WORDS = (
    "anuncia anunciou anunciam lanca lancou lancamento confirma confirmou confirmado preco precos "
    "hoje agora oficial oficialmente chega chegou chegam revela revelou atualizacao urgente acabou "
    "acaba comecou comeca liberado liberou disponivel "
    "announces announced launches launched launch confirms confirmed price prices today official "
    "reveals revealed update breaking now available rollout"
).split()

AI_WORDS = "ia inteligencia artificial chatgpt openai gemini copilot claude llm robo robos robot robots agente agentes algoritmo ai".split()

_SUSPENSE = frozenset(SUSPENSE_WORDS)
_URGENT = frozenset(URGENT_WORDS)
_AI = frozenset(AI_WORDS)


def _norm(text: str) -> list[str]:
    t = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode().lower()
    return re.findall(r"[a-z]+", t)


def mood_scores(text: str) -> dict[str, int]:
    """Pontos por clima no texto (titulo + roteiro)."""
    ws = _norm(text)
    return {
        "suspense": sum(1 for w in ws if w in _SUSPENSE),
        "urgent": sum(1 for w in ws if w in _URGENT),
        "ai": sum(1 for w in ws if w in _AI),
    }


def mood_for(theme: str, text: str, default: dict) -> dict:
    """Clima da trilha: `default` e THEMES[tema]["music"]; o texto pode trocar.

    - assunto pesado (vazamento, ataque, emprego, proibicao, guerra...) => suspense;
      com IA no assunto basta um sinal de risco.
    - anuncio, preco, lancamento, "hoje" sem peso => urgent (so em noticia/incidente).
    - curiosidade, historia, previsao e teoria mantem o clima do tema, a nao ser
      que o assunto seja claramente pesado.
    """
    s = mood_scores(text)
    heavy = s["suspense"] >= 2 or (s["ai"] >= 1 and s["suspense"] >= 1)
    if theme in ("tech_news", "story"):
        if heavy:
            return dict(MOODS["suspense"])
        if theme == "story":
            return dict(default)   # incidente ja vem tenso por padrao
        if s["urgent"] >= 1:
            return dict(MOODS["urgent"])
        return dict(default)
    if s["suspense"] >= 3:
        return dict(MOODS["suspense"])
    return dict(default)
