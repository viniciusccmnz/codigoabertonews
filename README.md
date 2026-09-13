# Usina de vídeos para TikTok (jornalismo de tecnologia)

Todo dia o algoritmo escolhe 5 assuntos de tecnologia com potencial de
viralizar, escreve um roteiro jornalístico com estrutura fixa, revisa o texto
com IA para ficar fluido, narra com voz animada e contínua, busca fotos e
vídeos livres (Pexels, Wikipedia), coloca o personagem do canal (um hacker de
capuz, só os olhos aparecem) na tela, escolhe o clima da trilha pelo assunto e
edita no formato do TikTok. Depois pode mandar o vídeo para o seu TikTok. Depois avalia o próprio vídeo (nota
de QA) e descarta o que não passa.

## IA e custo

A chave da OpenAI fica em `.env` (`OPENAI_API_KEY=...`), nunca é impressa.
Por vídeo, três chamadas, todas com cache:

| Uso | Modelo | Custo por vídeo (aprox.) |
|---|---|---|
| Revisão do roteiro (fluidez, transições, sem inventar fato) | gpt-4o-mini | US$ 0,001 |
| Narração (voz masculina, sotaque brasileiro, termos em inglês certos) | gpt-4o-mini-tts | US$ 0,02 |
| Tempo por palavra para a legenda | whisper-1 | US$ 0,008 |

5 vídeos por dia dão cerca de US$ 0,15 por dia. O gasto do dia aparece no fim
de cada `render`/`daily` e em `cache/ai/usage.json`. Sem a chave, tudo cai para
o caminho gratuito (voz Edge masculina, roteiro do template).

A revisão por IA é validada: mesma sequência de trechos, todos os números
preservados, 170 a 290 palavras. Se a resposta falhar duas vezes, fica o texto
do template.

**Voz.** Padrão `cedar` (masculina, natural, brasileira), com instrução de
locutor de tecnologia: ritmo contínuo, sem pausa dramática entre frases, e
velocidade 1,05x em notícia e incidente (`VOICE_SPEED` em `viral/config.py`).
O "segue o perfil" é dito de passagem, como um aparte no meio da história,
sem tom de encerramento. As pausas que sobram da síntese são cortadas por
detecção de silêncio real (máximo 0,26 s entre palavras). Alternativas: `ash`,
`verse`, `echo`. Troque com `--voice ash` ou mude `VOICE` em `viral/config.py`.

**Troque a chave** no painel da OpenAI depois de testar: ela circulou em texto
antes de entrar no `.env`.

## B-roll em vídeo (Pexels, grátis)

Os vídeos virais de notícia de tecnologia usam trechos de vídeo de banco
(mãos com celular, fábrica, tela de código, data center) entre as fotos da
matéria. O sistema já busca isso por trecho, com a consulta em inglês que o
próprio roteiro gera, mas precisa de uma chave gratuita do Pexels:

1. Crie conta em pexels.com/api (leva 2 minutos, sem cartão).
2. Copie a chave e acrescente ao `.env`: `PEXELS_API_KEY=...`

Limite gratuito: 200 pedidos por hora; 5 vídeos por dia usam uns 60. Sem a
chave, o sistema usa só vídeos do Wikimedia Commons cujo título cite o assunto
(raro) e, no resto, fotos da matéria, cartão de manchete e cartões de texto
com a última foto desfocada ao fundo.

## Comandos

Gerar os 5 do dia (1080x1920, prontos para postar):

```bash
python make.py daily --quality final
```

Gerar 1 vídeo de um tema:

```bash
python make.py render --theme tech_news --quality final
```

Temas: `tech_news`, `story` (incidente), `curiosity`, `history` (neste dia),
`prediction` (dados), `theory` (mito ou verdade).

Só o roteiro, a narração e a nota, sem vídeo (rápido, para conferir o texto):

```bash
python make.py render --theme theory --no-render
```

Ouvir as vozes e escolher (gera `output/vozes/ash.mp3`, `verse.mp3`,
`echo.mp3`, `cedar.mp3` com a chave da OpenAI, mais as três gratuitas do Edge):

```bash
python make.py voices
```

Depois use `--voice verse` (OpenAI) ou `--voice pt-BR-AntonioNeural` (Edge).

Ver o plano do dia sem gerar nada:

```bash
python make.py plan
```

Outros: `--seed 2` (outra variação), `--best-of 1` (mais rápido), `--min-qa 80`
(mais exigente), `--no-mascot` (sem o personagem), `--id fact-linux-hobby`
(tema específico, ids em `python make.py topics`), `python make.py cron`
(gera os 5 todo dia às 6h).

## O que sai

Em `output/AAAA-MM-DD/`, para cada vídeo:

| Arquivo | O que é |
|---|---|
| `NN-tema-titulo.mp4` | o vídeo (65 a 85 s, acima do mínimo para monetizar) |
| `NN-tema-titulo.txt` | descrição pronta: texto + hashtags + fonte + crédito de imagem (a fonte não aparece no vídeo) |
| `NN-tema-titulo.roteiro.txt` | o roteiro por trecho, para você gravar com a sua voz |
| `NN-tema-titulo_contato.jpg` | 12 quadros do vídeo para conferir sem abrir |
| `NN-tema-titulo.json` | roteiro, tempos, nota de QA, avisos, fontes |

## Sobre a voz e o selo de IA do TikTok

Qualquer voz sintética é voz gerada por computador. Se você quer o vídeo sem
nenhuma marca de IA, o caminho é gravar a narração:

1. Gere o roteiro: `python make.py render --theme tech_news --no-render --best-of 1`
2. Abra o `.roteiro.txt`, grave lendo os trechos na ordem, com meio segundo de
   pausa entre eles (celular no modo avião, qualquer gravador serve).
3. Gere o vídeo com a sua voz, mesmo tema e mesma semente:

```bash
python make.py render --id <id do tema> --seed 0 --narration C:\caminho\narracao.wav --quality final
```

O alinhamento com as legendas é feito por detecção das pausas, sem
reconhecimento de fala. A voz passa pela mesma cadeia de estúdio (corte de
graves, presença, compressão, um pouco de sala).

## Estrutura do roteiro

Gancho viral (1 frase) → aparte "segue o perfil" → contexto → desenvolvimento
(fatos na ordem da fonte, sem repetição) → virada (o dado mais forte) → "na
prática, o que muda para você" → pergunta para comentar → fechamento (salvar
ou comentar, "amanhã tem mais"). Curiosidade traz contexto da época e "por que
importa hoje"; previsão traz "o que isso significa" e "o que pode quebrar a
curva"; teoria traz origem, o que dizem, o que está documentado e veredito.

As regras seguem o que retém no TikTok e no Reels (guias de 2025-2026 sobre
ganchos e ritmo): o algoritmo decide em 1,5 s e o gancho tem que estar
resolvido aos 3 s, então em notícia e incidente ele tem no máximo 12 palavras
e começa pelo dado mais forte (número exato, nome, consequência), sem "hoje
vamos falar". Cada fato carrega um dado concreto e, quando o material permite,
cruza dados (proporção, antes e depois, "o dobro", "de cada 10"), sem inventar
número novo. A virada fica entre o terceiro e o quinto fato, onde a atenção
cai. O "segue o perfil" é um aparte curto (até 9 palavras) no meio do vídeo,
não um encerramento, e o fechamento não pede para seguir de novo.

Textos em `data/templates.json` (ganchos, viradas, "na prática", chamadas),
`data/facts.json` (50 curiosidades), `data/theories.json` (13 mitos e teorias),
`data/trends.json` (11 séries com fonte).

## Como o algoritmo escolhe o assunto

1. 8 feeds brasileiros e 12 internacionais (The Verge, Ars Technica, TechCrunch,
   BBC, Wired, BleepingComputer, Reddit...).
2. **Calor**: a mesma história em várias redações do mundo e do Brasil, casada
   por nomes, números e um glossário pt-en.
3. **Polêmica**: demissão, processo, vazamento, preço, proibição, privacidade,
   IA no emprego, Musk...
4. **Penalidades**: review, cupom, oferta, lista, comparativo, matéria
   institucional, título sem termo de tecnologia.
5. **Sem repetição**: o que já saiu não volta, nem com outro título, nem a
   mesma empresa duas vezes no mesmo dia. Sem notícia forte, entra curiosidade
   ou teoria inédita.

O dia sai com: 2 notícias, 1 incidente (ou notícia), 1 curiosidade ou teoria,
1 "neste dia" ou previsão.

## A edição

- Narração contínua com pausas apertadas, tempo por palavra.
- Legenda karaokê (3 palavras por linha, palavra dita em destaque).
- Foto livre (Pexels, Wikipedia/Commons) → cartão de texto → foto; quando a
  mesma foto continua, o próximo plano é um reenquadramento mais fechado, como
  um editor faria. **Nunca** foto de site de notícia ou de canal: é
  copyright, e o TikTok derruba.
- **Escolha das imagens** (`_visuals_for` em `viral/pipeline.py`): a foto de uma
  pessoa ou máquina entra no plano em que o nome é falado na frase. O trecho de
  contexto nunca mostra foto da história antes de ela chegar (nem cópia do mesmo
  arquivo). Nada de ir e voltar entre fotos (A, B, A). Nome de pessoa não vai
  para banco de vídeo genérico; clipe Pexels sem relação com o assunto é
  descartado. No Commons ficam de fora filme, pintura, logo, mapa e bandeira.
  Sem foto boa, o trecho vira cartão: é melhor que foto errada.
- Nenhuma fonte escrita na tela: a fonte vai na descrição (está no `.txt`).
- "IA" é falado "i-á".
- Personagem do canal (hacker de capuz magro, desenhado por código, fiel ao
  logo): grande na tela, entra deslizando na diagonal pelo canto de baixo,
  alternando o lado a cada aparição. Sem boca: rosto na sombra, só os olhos
  azuis. Só braço e olho mexem (aponta, acena, cruza os braços, piscada e
  formato dos olhos). Enquanto ele está na tela, legendas e cartões vão para o
  lado livre, sem sobrepor o personagem. Aparece também nos fatos com número
  que ficaram sem foto.
- A chamada "segue o perfil" na tela é um selo pequeno de 1,6 s, não um cartão
  de fim de vídeo.
- Gancho com impacto e tremor de câmera; virada com mergulho a preto, riser e
  flash; "na prática" com mergulho e carimbo; números com contagem.
- Círculo ou seta só quando a foto tem alvo claro; gráfico animado nas previsões.
- Efeitos sonoros baixos e só onde marcam algo; trilha por camadas com ducking.
- **Clima da trilha pelo assunto** (`viral/audio/mood.py`): notícia sobre IA
  preocupante, vazamento, ataque, demissão, proibição, guerra, golpe → `suspense`
  (92 bpm, menor, drone, tique de relógio, batida de coração, riser e impacto
  em cima da virada do roteiro). Anúncio, lançamento, preço, "hoje" → `urgent`
  (122 bpm). Sem sinal forte, fica o clima do tema: `synthwave` (notícia),
  `lofi` (curiosidade), `cinematic` (neste dia), `arp` (previsão, teoria).
  O clima escolhido sai no log (`trilha: suspense 92 bpm, virada aos 41.2s`) e no
  `.json`. Amostras de 20 s em `output/trilhas/`.
  Músicas suas livres de direitos em `assets/music/<estilo>/` (`suspense`,
  `urgent`, `synthwave`, `lofi`, `cinematic`, `arp`) passam na frente.

## QA automático

Até 3 variações por vídeo, fica a de maior nota. Mede: repetição, ritmo e
pausas, duração (abaixo de 60 s perde 15 pontos), efeitos demais, plano parado
(acima de 5,5 s) e poucos cortes (o TikTok pede corte a cada 1,5 a 3 s),
poucas imagens, foto repetida, punchline e gancho compridos, gancho que
termina depois de 4,5 s, mais de 15 s seguidos sem quebra de padrão (virada,
personagem ou punchline), fatos sem número nem nome próprio. Abaixo de
`--min-qa` (70) o `daily` pula para outro tema.

## Requisitos

Python 3.10+, `numpy`, `pillow`, `requests`, `edge-tts`, `ffmpeg` no PATH
(`python make.py setup` resolve o que faltar). Internet para feeds, Wikipedia,
fotos e voz. Sem internet: `--engine sapi --no-images`.

## Direitos

Trilha e efeitos: síntese própria. Personagem: desenho próprio. Fontes: OFL.
Fotos e vídeos: Pexels (licença livre) e Wikipedia/Commons (licença livre,
crédito no `.txt`). Nenhuma imagem de site de notícia ou canal.

## Publicar no TikTok

O sistema manda o vídeo pronto para a sua conta pela API oficial do TikTok.
Antes, o que a API permite e o que não permite, sem enfeite:

- **Dá para enviar como rascunho** (`inbox`): o vídeo aparece na sua caixa de
  entrada do app, você abre, cola a legenda (o `.txt`), confere e publica. É o
  caminho mais seguro e o padrão.
- **Dá para publicar direto** (`direct`): legenda, privacidade e selo de IA
  vão junto. Mas **enquanto o app não passar pela auditoria do TikTok, todo
  vídeo publicado direto fica privado** (só você vê). A auditoria é pedida no
  portal de desenvolvedor, depois de o app estar funcionando; leva alguns dias.
- **A API não agenda** para uma hora futura. Quem agenda é o computador: o
  comando `tiktok-cron` registra tarefas no Agendador do Windows que rodam
  `publish --one` nos horários que você escolher (o PC precisa estar ligado).
  Se preferir agendar dentro do TikTok, use o rascunho e marque a data no
  TikTok Studio (web), que aceita até 10 dias à frente.
- Limite: 6 chamadas por minuto por conta e um teto diário de posts por
  usuário definido pelo TikTok (erro `spam_risk_too_many_posts` quando passa).

### Passo a passo (uma vez só)

1. Entre em developers.tiktok.com com a conta do canal e crie um app
   ("Manage apps" → "Connect an app"). Nome e descrição livres.
2. Em "Add products", adicione **Login Kit** e **Content Posting API**. Em
   Content Posting API, ligue "Direct Post".
3. Em Login Kit, coloque a URL de retorno: `https://papiro.work/tiktok`
   (não precisa existir página; só precisa ser https e bater com a do `.env`).
   Em "Scopes", marque `user.info.basic`, `video.upload`, `video.publish`.
4. Copie **Client key** e **Client secret** para o `.env`, uma linha cada:

```
TIKTOK_CLIENT_KEY=...
TIKTOK_CLIENT_SECRET=...
```

   Opcional: `TIKTOK_REDIRECT_URI=...` se usar outra URL de retorno.

5. Faça o login (abre o navegador; você autoriza; o TikTok manda para a URL
   de retorno com um código na barra de endereço; copie a URL inteira e cole no
   terminal):

```bash
python make.py tiktok-login
```

   O token fica em `cache/tiktok_token.json` (não compartilhe, já está fora do
   git) e renova sozinho por até 1 ano.

6. Confira:

```bash
python make.py tiktok-status
```

### No dia a dia

Ver o que seria enviado, sem mandar nada:

```bash
python make.py publish --dry-run
```

Mandar os vídeos de hoje como rascunho para o app:

```bash
python make.py publish
```

Só o próximo ainda não enviado (o `publicados.json` da pasta do dia evita
repetir):

```bash
python make.py publish --one
```

Publicar direto (privado até a auditoria; depois use `--privacy PUBLIC_TO_EVERYONE`):

```bash
python make.py publish --mode direct --privacy SELF_ONLY
```

Agendar no Windows: um vídeo às 9h, 12h, 15h, 18h e 21h, todo dia (`daily`
gera os 5 às 6h, o `publish --one` manda um por horário):

```bash
python make.py tiktok-cron --at 09:00,12:00,15:00,18:00,21:00
```

`--dry-run` mostra as tarefas antes de criar; `--remove` apaga. Consultar o
estado de um envio pendente: `python make.py publish --check`. Log em
`cache/tiktok_publish.log` (sem token).

Selo de IA: em `direct`, o vídeo vai marcado como conteúdo gerado por IA
(`is_aigc`), porque a voz é sintética. `--no-aigc` tira a marca; só use com
narração gravada por você.
