"""Usina de videos virais para TikTok, sem depender de IA.

Uso:
  python make.py setup                      instala dependencias e confere ffmpeg/fontes
  python make.py voices                     gera amostras das vozes pt-BR para voce escolher
  python make.py topics [--all]             lista os temas candidatos de hoje, pontuados
  python make.py plan                       mostra o plano de 5 videos do dia
  python make.py demo                       gera 1 video offline-friendly (curiosidade) em 720p
  python make.py render --theme curiosity   gera 1 video do tema (ou --id <topic_id>, --query "texto")
  python make.py daily [--quality final]    gera os 5 videos do dia em output/AAAA-MM-DD/
  python make.py cron                       loop: a cada dia gera os 5 (deixa rodando)

TikTok (precisa de TIKTOK_CLIENT_KEY e TIKTOK_CLIENT_SECRET no .env; veja o README):
  python make.py tiktok-login               autoriza o canal (abre o navegador; voce cola a URL de retorno)
  python make.py tiktok-status              conta conectada, privacidades disponiveis, auditoria do app
  python make.py publish [--dry-run]        publica os MP4 de output/<hoje>/ (legenda = .txt ao lado)
      --date AAAA-MM-DD  --mode inbox|direct  --privacy SELF_ONLY|PUBLIC_TO_EVERYONE|...
      --at 09:00,12:00   um video por horario (espera cada um)   --one  so o proximo   --check  consulta pendentes
  python make.py tiktok-cron [--at ...]     registra no Agendador do Windows um "publish --one" por horario (--remove desfaz)

Opcoes: --quality poc|final  --seed N  --voice pt-BR-FranciscaNeural  --engine auto|edge|sapi  --no-images
"""
from __future__ import annotations

import argparse
import subprocess
import sys
import time
from datetime import date, datetime
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).resolve().parent))

from viral.config import OUTPUT, QUALITY, VOICE, VOICE_RATE  # noqa: E402


def log(msg: str) -> None:
    print(msg, flush=True)


def cmd_setup(a):
    log("Instalando dependencias (numpy, pillow, requests, edge-tts)...")
    subprocess.run([sys.executable, "-m", "pip", "install", "-q", "numpy", "pillow", "requests", "edge-tts"], check=False)
    import shutil
    if shutil.which("ffmpeg"):
        log("ffmpeg: ok")
    else:
        log("ffmpeg NAO encontrado. Instale: winget install Gyan.FFmpeg  (ou baixe em ffmpeg.org e coloque no PATH)")
    from viral.config import FONTS
    missing = [f for f in ("Montserrat-ExtraBold.ttf", "Montserrat-Bold.ttf", "JetBrainsMono-Bold.ttf") if not (FONTS / f).exists()]
    if missing:
        log("Baixando fontes livres (OFL)...")
        import requests
        urls = {
            "Montserrat-ExtraBold.ttf": "https://github.com/JulietaUla/Montserrat/raw/master/fonts/ttf/Montserrat-ExtraBold.ttf",
            "Montserrat-Bold.ttf": "https://github.com/JulietaUla/Montserrat/raw/master/fonts/ttf/Montserrat-Bold.ttf",
            "JetBrainsMono-Bold.ttf": "https://github.com/JetBrains/JetBrainsMono/raw/master/fonts/ttf/JetBrainsMono-Bold.ttf",
        }
        for f in missing:
            try:
                (FONTS / f).write_bytes(requests.get(urls[f], timeout=60).content)
                log(f"  {f}: ok")
            except Exception as e:
                log(f"  {f}: falhou ({type(e).__name__}); vai usar fonte do Windows")
    log("Pronto. Teste com: python make.py demo")


def cmd_voices(a):
    from viral.voice import tts
    out = OUTPUT / "vozes"
    text = "Isso pode mudar o que você paga pelo seu celular. Segue o canal para não perder as próximas. O caso é o seguinte: a Apple vai pagar 250 dólares por cada tela dobrável."
    log(f"Gerando amostras em {out} (ouça e escolha; depois use --voice NOME):")
    tts.make_samples(out, text, log)


def _gather(a):
    from viral import planner
    return planner.gather(log=log, max_age_hours=a.max_age)


def cmd_topics(a):
    topics = _gather(a)
    log(f"\n{len(topics)} temas candidatos (novos):")
    shown = topics if a.all else topics[:40]
    for t in shown:
        log(f"  {t.score:6.1f}  {t.theme:10s}  {t.id:22s}  {t.title[:80]}")


def cmd_plan(a):
    from viral import planner
    topics = _gather(a)
    plan = planner.plan_day(topics, 5, rng_seed=int(date.today().strftime('%Y%m%d')) + a.seed)
    log("\nPlano de hoje (5 videos):")
    for i, t in enumerate(plan, 1):
        log(f"  {i}. [{t.theme}] {t.title[:90]}  (score {t.score}, id {t.id})")
    return plan


def _pick_topic(a):
    from viral import planner
    from viral.sources import facts, predictions
    if a.id:
        if a.id.startswith("fact-"):
            pool = facts.load_topics()
        elif a.id.startswith("theory-"):
            pool = facts.load_theories()
        elif a.id.startswith("trend-"):
            pool = predictions.load_topics(date.today().year)
        else:
            pool = _gather(a)
        for t in pool:
            if t.id == a.id:
                return t
        raise SystemExit(f"tema nao encontrado: {a.id}")
    if a.theme in ("curiosity", "prediction", "theory") and not a.query:
        pool = {"curiosity": facts.load_topics, "theory": facts.load_theories, "prediction": lambda: predictions.load_topics(date.today().year)}[a.theme]()
        used = planner.load_history()["used"]
        fresh = [t for t in pool if t.id not in used] or pool
        import random
        return random.Random(a.seed + int(date.today().strftime('%Y%m%d'))).choice(fresh)
    topics = _gather(a)
    if a.query:
        q = a.query.lower()
        topics = [t for t in topics if q in (t.title + " " + " ".join(t.sentences)).lower()]
        if not topics:
            raise SystemExit("nenhum tema casa com a busca")
    if a.theme:
        topics = [t for t in topics if t.theme == a.theme] or topics
    return topics[0]


def cmd_render(a):
    from viral.pipeline import LowQuality, produce
    from viral import ai
    cands = _pick_topics(a)
    meta = None
    for t in cands:
        log(f"Tema: [{t.theme}] {t.title}")
        try:
            meta = produce(t, a.quality, a.seed, a.voice, VOICE_RATE, None, 1, a.engine, log, a.no_images, not a.no_render, a.best_of, a.min_qa, a.narration, not a.no_mascot)
            break
        except LowQuality as e:
            log(f"  descartado: {e}\n")
    if meta is None:
        raise SystemExit("nenhum tema atingiu a nota minima; tente outro tema ou --min-qa menor")
    log(f"\nVideo: {OUTPUT / date.today().isoformat() / meta['file']}")
    log("Legenda sugerida:\n" + meta["caption"])
    log("Gasto OpenAI " + ai.usage_summary())


def _pick_topics(a):
    """Ate 3 candidatos, em ordem, para o render pular tema fraco."""
    first = _pick_topic(a)
    if a.id or a.query or a.theme in ("curiosity", "prediction", "theory"):
        return [first]
    from viral import planner
    topics = _gather(a)
    same = [t for t in topics if t.theme == first.theme and t.id != first.id]
    return [first] + same[:2]


def cmd_demo(a):
    from viral.sources import facts
    from viral.pipeline import produce
    pool = facts.load_topics()
    t = next((x for x in pool if x.id == "fact-mariposa-bug"), pool[0])
    log(f"Demo: {t.title}")
    meta = produce(t, a.quality, a.seed, a.voice, VOICE_RATE, OUTPUT / "demo", 1, a.engine, log, a.no_images, not a.no_render, a.best_of, a.min_qa, a.narration, not a.no_mascot)
    log(f"\nVideo: {OUTPUT / 'demo' / meta['file']}")


def _video_worker(job_path: str, result_path: str) -> None:
    """Processo filho do daily: produz um video e termina, devolvendo ao sistema toda a memoria do render."""
    import pickle
    from viral.pipeline import LowQuality, produce
    job = pickle.loads(Path(job_path).read_bytes())
    a, cand = job["args"], job["topic"]
    try:
        meta = produce(cand, a.quality, a.seed, a.voice, VOICE_RATE, job["out_dir"], job["index"], a.engine, log, a.no_images, not a.no_render, a.best_of, a.min_qa, a.narration, not a.no_mascot)
        res = {"status": "ok", "meta": meta}
    except LowQuality as e:
        res = {"status": "low", "error": str(e)}
    except Exception as e:
        res = {"status": "error", "error": f"{type(e).__name__}: {e}"}
    Path(result_path).write_bytes(pickle.dumps(res))


def _produce_isolated(a, cand, out_dir: Path, index: int) -> dict:
    """Um video num processo filho (Windows nao tem fork: python novo, com o pedido num arquivo temporario).
    A saida do filho segue no log do pai. Devolve {"status": "ok"|"low"|"error", "meta" | "error"}."""
    import os
    import pickle
    import tempfile
    with tempfile.TemporaryDirectory(prefix="daily_") as tmp:
        job, res = Path(tmp) / "job.pkl", Path(tmp) / "result.pkl"
        job.write_bytes(pickle.dumps({"args": a, "topic": cand, "out_dir": out_dir, "index": index}))
        env = dict(os.environ, PYTHONUNBUFFERED="1", PYTHONIOENCODING="utf-8")
        proc = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), "_video", str(job), str(res)],
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace", env=env)
        for line in proc.stdout:
            log(line.rstrip("\n"))
        proc.wait()
        if not res.exists():
            return {"status": "error", "error": f"processo do video terminou sem resultado (codigo {proc.returncode})"}
        return pickle.loads(res.read_bytes())


def cmd_daily(a):
    from viral import planner
    topics = _gather(a)
    plan = planner.plan_day(topics, a.count, rng_seed=int(date.today().strftime('%Y%m%d')) + a.seed)
    out_dir = OUTPUT / date.today().isoformat()
    log(f"\nGerando {len(plan)} videos em {out_dir}")
    done = []
    used_ids = set()
    for i, t in enumerate(plan, 1):
        # se o tema nao atinge a nota minima, tenta o proximo candidato do mesmo tipo (ate 3 tentativas)
        cands = [t] + [o for o in topics if o.theme == t.theme and o.id != t.id and o.id not in used_ids][:2]
        for cand in cands:
            used_ids.add(cand.id)
            try:
                r = _produce_isolated(a, cand, out_dir, i)   # um processo por video: a memoria do render nao acumula
            except Exception as e:
                r = {"status": "error", "error": f"{type(e).__name__}: {e}"}
            if r["status"] == "ok":
                done.append(r["meta"])
                break
            if r["status"] == "low":
                log(f"  descartado {cand.id}: {r['error']}")
            else:
                log(f"  FALHOU {cand.id}: {r['error']}")
    log(f"\n{len(done)} videos prontos:")
    for m in done:
        log(f"  {m['file']}  ({m['duration']}s)  [{m['theme']}]")
    log("Legendas em .txt ao lado de cada .mp4. Horarios sugeridos (BRT): 07:30, 12:00, 18:00, 20:30, 22:00")


def cmd_cron(a):
    last = None
    while True:
        today = date.today()
        if today != last and datetime.now().hour >= a.hour:
            log(f"=== {today} ===")
            try:
                cmd_daily(a)
            except Exception as e:
                log(f"falhou: {e}")
            last = today
        time.sleep(600)


def cmd_tiktok_login(a):
    from viral.publish import tiktok
    # o TikTok exige redirect https registrado: o fluxo e sempre manual (--manual e aceito por compatibilidade)
    raise SystemExit(tiktok.login(log, open_browser=not a.no_browser))


def cmd_tiktok_status(a):
    from viral.publish import tiktok
    raise SystemExit(tiktok.status_report(log))


NETWORKS = ("tiktok", "instagram")


def cmd_publish(a):
    """Posta os videos pendentes do dia nas redes de --to. Com --at, cada horario posta UM video por rede
    (o processo fica esperando; e o que o agendador das 5 dispara). Sem --at, posta tudo agora (ou --one)."""
    from viral.publish import instagram, tiktok
    day = a.date or tiktok.today()
    nets = [n.strip().lower() for n in (a.to or ",".join(NETWORKS)).split(",") if n.strip()]
    bad = [n for n in nets if n not in NETWORKS]
    if bad:
        log("--to invalido: use tiktok, instagram ou tiktok,instagram")
        raise SystemExit(2)
    if a.check:
        raise SystemExit(tiktok.check_pending(day, log))
    try:
        times = tiktok._parse_times(a.at)
    except ValueError:
        log("--at invalido: use HH:MM separados por virgula, ex. --at 12:00,18:00")
        raise SystemExit(2)

    def run_once(one: bool) -> int:
        codes = []
        if "tiktok" in nets:
            codes.append(tiktok.publish_day(day, a.mode, a.privacy, None, one, a.dry_run, not a.no_aigc, log))
        if "instagram" in nets:
            codes.append(instagram.publish_day(day, one, a.dry_run, log))
        return max(codes) if codes else 0

    if not times or a.dry_run:
        if times:
            log(f"[simulacao] horarios: {', '.join(times)} (um video por rede em cada)")
        raise SystemExit(run_once(a.one or bool(times)))
    code = 0
    for t in times:
        if not tiktok._wait_until(t, log):
            log(f"Horario {t} ja passou hoje: pulado.")
            continue
        code = max(code, run_once(True))
    raise SystemExit(code)


def cmd_kit(a):
    from viral.config import OUTPUT
    from viral.publish import kit, tiktok
    folder = OUTPUT / (a.date or tiktok.today())
    n = kit.build_day(folder, log)
    log(f"{n} kits em {folder}")


def cmd_tiktok_cron(a):
    from viral.publish import tiktok
    raise SystemExit(tiktok.cron_install(a.at, a.mode, a.privacy, a.remove, a.dry_run, log))


def main():
    if sys.argv[1:2] == ["_video"]:   # interno: processo filho do daily (python make.py _video <pedido> <resultado>)
        return _video_worker(sys.argv[2], sys.argv[3])
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("cmd", choices=["setup", "voices", "topics", "plan", "demo", "render", "daily", "cron",
                                   "tiktok-login", "tiktok-status", "publish", "tiktok-cron", "kit"])
    p.add_argument("--date", help="publish: pasta output/AAAA-MM-DD (padrao: hoje)")
    p.add_argument("--mode", choices=["inbox", "direct"], default="inbox", help="publish: inbox = rascunho no app do TikTok; direct = publica direto")
    p.add_argument("--privacy", default="SELF_ONLY", help="publish --mode direct: SELF_ONLY, PUBLIC_TO_EVERYONE, MUTUAL_FOLLOW_FRIENDS, FOLLOWER_OF_CREATOR")
    p.add_argument("--at", help="publish/tiktok-cron: horarios HH:MM separados por virgula")
    p.add_argument("--to", help="publish: redes separadas por virgula (padrao: tiktok,instagram)")
    p.add_argument("--one", action="store_true", help="publish: so o proximo video ainda nao publicado")
    p.add_argument("--dry-run", action="store_true", help="publish/tiktok-cron: mostra o que faria, sem rede")
    p.add_argument("--check", action="store_true", help="publish: consulta o status dos envios pendentes")
    p.add_argument("--no-aigc", action="store_true", help="publish --mode direct: nao marcar como conteudo gerado por IA")
    p.add_argument("--manual", action="store_true", help="tiktok-login: fluxo manual (e o unico: aceito por compatibilidade)")
    p.add_argument("--no-browser", action="store_true", help="tiktok-login: nao abrir o navegador, so mostrar o endereco")
    p.add_argument("--remove", action="store_true", help="tiktok-cron: apaga as tarefas agendadas")
    p.add_argument("--quality", choices=list(QUALITY), default="poc")
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--voice", default=VOICE)
    p.add_argument("--engine", choices=["auto", "openai", "edge", "sapi"], default="auto", help="auto = OpenAI se houver chave no .env, senao Edge")
    p.add_argument("--theme", choices=["tech_news", "story", "curiosity", "history", "prediction", "theory"])
    p.add_argument("--narration", help="sua narracao gravada (wav/mp3) lendo o .roteiro.txt; alinhada por pausas, sem IA")
    p.add_argument("--no-mascot", action="store_true", help="sem o personagem")
    p.add_argument("--id")
    p.add_argument("--query")
    p.add_argument("--count", type=int, default=5)
    p.add_argument("--max-age", type=float, default=72.0, help="idade maxima da noticia em horas")
    p.add_argument("--hour", type=int, default=6, help="cron: hora do dia para gerar")
    p.add_argument("--all", action="store_true")
    p.add_argument("--no-images", action="store_true", help="so fundos gerados (sem internet para imagens)")
    p.add_argument("--no-render", action="store_true", help="so roteiro, narracao e QA (sem video)")
    p.add_argument("--best-of", type=int, default=3, help="variacoes testadas por video; fica a melhor nota de QA")
    p.add_argument("--min-qa", type=int, default=70, help="nota minima de QA; abaixo disso o daily pula para outro tema")
    a = p.parse_args()
    {"setup": cmd_setup, "voices": cmd_voices, "topics": cmd_topics, "plan": cmd_plan, "demo": cmd_demo, "render": cmd_render, "daily": cmd_daily, "cron": cmd_cron,
     "tiktok-login": cmd_tiktok_login, "tiktok-status": cmd_tiktok_status, "publish": cmd_publish, "tiktok-cron": cmd_tiktok_cron, "kit": cmd_kit}[a.cmd](a)


if __name__ == "__main__":
    main()
