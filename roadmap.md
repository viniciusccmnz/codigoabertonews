# Roadmap: do zero aos tres perfis publicando

Este arquivo e a ordem de execucao. O que colar em cada campo esta em
[`perfis.md`](perfis.md); aqui esta **quando**, **onde clicar** e **o que ja
precisa estar pronto** antes de abrir cada site.

A ordem importa por um motivo pratico: Instagram e TikTok pedem foto de perfil e
@ no meio do cadastro, e conta criada com campo em branco fica com o cadastro
pela metade por semanas. Entao tudo que e arquivo se gera antes.

---

## Fase 0 — Antes de abrir qualquer site (uma sessao, ~40 min)

### 0.1 Gere as imagens de marca

```bash
npm run social -- capa-linkedin
```

```bash
npm run social -- destaques
```

Isso escreve em `saida/social/marca/`:

| Arquivo | Onde entra |
|---|---|
| `capa-linkedin-1128x191.png` | capa da pagina do LinkedIn |
| `destaques/como-funciona.png` | capa do destaque 1 do Instagram |
| `destaques/hora-extra.png` | destaque 2 |
| `destaques/intervalo.png` | destaque 3 |
| `destaques/prova.png` | destaque 4 |
| `destaques/direitos.png` | destaque 5 |

O logo e o avatar ja existem no repositorio, nao precisam ser gerados:

| Arquivo | Onde entra |
|---|---|
| `public/brand/papiro-logo-1024.png` | logo da pagina do LinkedIn |
| `public/brand/papiro-avatar-1024.png` | foto de perfil do Instagram e do TikTok |

Junte os oito arquivos numa pasta so. Voce vai subir do celular em duas das tres
redes, entao mande essa pasta para o celular agora — procurar arquivo no meio do
cadastro e como se perde a paciencia e se aceita o padrao da plataforma.

### 0.2 Tenha um e-mail proprio da marca

Nao use o pessoal. `contato@papiro.work` ou `social@papiro.work`. Duas razoes:
recuperacao de conta e a coisa mais chata que existe nessas plataformas, e o
e-mail aparece publicamente no botao de contato do Instagram.

Se ainda nao existir, crie antes. Cadastro comecado com e-mail pessoal so se
troca depois, com verificacao.

### 0.3 Reserve os cinco handles

Ordem de tentativa e justificativa estao na secao 0 de [`perfis.md`](perfis.md).
Resumo: `papirowork` -> `papiroapp` -> `usepapiro` -> `papiroprova` ->
`papiro.work`.

**Reserve os cinco no Instagram e no TikTok**, mesmo os que nao vai usar. Conta
vazia com o @ da sua marca na mao de terceiro nao se recupera sem processo. Sao
cinco cadastros de dois minutos hoje contra um problema insoluvel depois.

No LinkedIn nao ha o que reservar: a URL publica e da pagina que ja existe.

**Checkpoint da fase 0:** oito arquivos na mao, e-mail da marca funcionando,
handle principal confirmado nas tres. So depois disso abra a proxima fase.

---

## Fase 1 — LinkedIn (a pagina que ja existe)

**Nao crie outra pagina.** O porque esta na secao 1 de [`perfis.md`](perfis.md);
em uma linha: pagina nova perde seguidor, historico e dominio, e a pesquisa por
"Papiro" passa a devolver duas, das quais uma esta morta.

### 1.1 Entre como administrador

`linkedin.com` -> foto no topo direito -> a pagina do Papiro na lista de paginas
que voce administra.

Se voce nao for admin e alguem for, peca acesso antes de continuar (o admin
atual faz isso em Ferramentas de administrador -> Gerenciar administradores). Se
a pagina estiver orfa, o LinkedIn tem um fluxo de reivindicacao que pede e-mail
no dominio da empresa — mais um motivo para a fase 0.2.

### 1.2 Editar pagina

**Ferramentas de administrador** -> **Editar pagina**. Os nomes das abas mudam
de tempos em tempos; o conteudo nao. Preencha nesta ordem, com os textos de
[`perfis.md`](perfis.md):

1. **Identidade da pagina** — logo (`papiro-logo-1024.png`), capa
   (`capa-linkedin-1128x191.png`), nome, URL publica.
2. **Detalhes** — slogan, site, setor, tamanho, tipo, ano de fundacao, sede.
3. **Sobre** — o texto longo. Cole inteiro; o LinkedIn corta a exibicao e abre
   no "ver mais", entao as tres primeiras linhas sao as unicas garantidas. Elas
   ja estao escritas pensando nisso.
4. **Especialidades** — as doze, uma a uma.
5. **Hashtags da pagina** — as tres.
6. **Botao personalizado** — "Saiba mais" apontando para `https://papiro.work`.

Salve. Se ele reclamar de campo obrigatorio vazio numa aba em que voce nao
mexeu, e quase sempre "Sede".

### 1.3 Limpe o passado

Se a pagina tem publicacoes que nao batem com o produto de hoje, apague **as
publicacoes**, uma a uma. Nao a pagina. Quem chega hoje le a primeira tela e
decide ali.

### 1.4 Publique no mesmo dia

O LinkedIn trata pagina que edita tudo e nao publica como pagina abandonada. O
lote da fase 4 serve.

---

## Fase 2 — Instagram

### 2.1 Crie a conta

No **celular**, pelo aplicativo. Cadastro por navegador funciona, mas a conversao
para conta profissional e os destaques sao mais simples no aplicativo.

E-mail da marca, senha nova (nao reaproveite), @ `papirowork`.

### 2.2 Converta para conta profissional

Perfil -> menu -> **Configuracoes e privacidade** -> **Tipo de conta e
ferramentas** -> **Mudar para conta profissional** -> escolha **Empresa**, nao
"Criador de conteudo".

Empresa libera o botao de contato e o link sem a limitacao de categoria de
criador. Categoria: `Aplicativo de produtividade`.

### 2.3 Preencha o perfil

**Editar perfil**, com os textos de [`perfis.md`](perfis.md): foto
(`papiro-avatar-1024.png`), nome, nome de usuario, bio, link, botao de acao
(E-mail).

O campo **Nome** carrega palavra-chave de proposito (`Papiro | Prova
trabalhista`): a busca do Instagram le nome e @, nao le a bio.

### 2.4 Monte os cinco destaques

Um destaque so existe se houver um story dentro dele. Entao:

1. Publique cinco stories quaisquer — pode ser um slide do primeiro lote, cinco
   vezes. Eles somem em 24h; o destaque continua.
2. Perfil -> `+` na linha de destaques -> selecione um story -> nome do destaque
   -> **Editar capa** -> escolha o PNG correspondente em `destaques/`.
3. Repita para os cinco: Como funciona, Hora extra, Intervalo, Prova, Direitos.

O Instagram mostra o mais recente primeiro. Crie na ordem inversa se quiser
"Como funciona" na esquerda, ou reordene depois arrastando.

---

## Fase 3 — TikTok

### 3.1 Crie a conta

Aplicativo, e-mail da marca, @ `papirowork`.

### 3.2 Converta para conta empresarial

Perfil -> menu -> **Configuracoes e privacidade** -> **Conta** -> **Mudar para
conta empresarial** -> categoria `Aplicativo e software`.

E a conta empresarial que libera o **link no perfil** e as estatisticas. Sem
ela, o link nao aparece e o perfil vira beco sem saida.

### 3.3 Preencha o perfil

**Editar perfil**, com os textos de [`perfis.md`](perfis.md). A bio do TikTok e
o campo mais apertado das tres (80 caracteres) — o texto ja esta cortado para
caber, nao acrescente nada.

---

## Fase 4 — Primeiro lote de conteudo

### 4.1 Gere

```bash
npm run social -- 6 --semente=lancamento
```

Sai em `saida/social/<data>-lancamento/`, uma pasta por peca:

```
01-hora-extra--conta/
  01.png ... 06.png            carrossel 1080x1350
  legenda.txt                  uma versao por rede, com as hashtags
  video/
    hora-extra--conta.mp4      vertical 1080x1920, 30fps
```

### 4.2 Olhe antes de publicar

Abra as seis. Peca que nao convence se joga fora: o historico **nao** foi
tocado, entao a combinacao volta para o estoque. Se quiser mais opcoes, gere com
outra semente.

Gerar e publicar sao dois passos de proposito. Se o gerar ja marcasse no
historico, um lote descartado queimaria as combinacoes para sempre.

### 4.3 Publique

| Rede | O que sobe | Onde |
|---|---|---|
| Instagram | as `NN.png` como carrossel | aplicativo, ordem 01 a 06 |
| Instagram | o `.mp4` como Reels | aplicativo |
| TikTok | o `.mp4` | aplicativo, ou `tiktok.com/upload` no computador |
| LinkedIn | as `NN.png` como documento | navegador |

A legenda de cada rede esta em `legenda.txt`, ja com as hashtags certas. As do
LinkedIn sao so tres de proposito: la, excesso de hashtag derruba alcance em vez
de aumentar.

**Ordem no dia:** TikTok e Instagram primeiro, que pesam a primeira hora;
LinkedIn de manha, em dia util.

### 4.4 Registre

Depois de publicado, e so depois:

```bash
npm run social -- 6 --semente=lancamento --registrar
```

Regera identico e marca as seis combinacoes como usadas. A partir dai elas nao
voltam a sair.

---

## Fase 5 — A rotina semanal

Segunda de manha, uma sessao de ~30 minutos:

1. `npm run social -- 3 --semente=s2026-38` (a semana como semente).
2. Descarta o que nao convence.
3. Agenda as tres. Instagram e LinkedIn agendam nativo; o TikTok agenda pelo
   computador, em `tiktok.com/upload`.
4. Publicado: roda de novo com a mesma semente e `--registrar`.

Tres por semana e o ritmo que o banco sustenta sem repetir: sao 176
combinacoes, ou pouco mais de **um ano** antes de qualquer reciclagem.

Para conferir quanto sobrou, qualquer geracao imprime no fim:

```
Estoque: 170 de 176 combinacoes ineditas restantes.
```

---

## Calendario das primeiras quatro semanas

| Quando | O que |
|---|---|
| Dia 1 | Fase 0 inteira: imagens, e-mail, cinco handles reservados |
| Dia 2 | Fase 1: LinkedIn editado e primeiro post no ar |
| Dia 3 | Fase 2: Instagram criado, convertido, cinco destaques montados |
| Dia 4 | Fase 3: TikTok criado e convertido |
| Dia 5 | Fase 4: lote de lancamento nas tres |
| Semanas 2 a 4 | Fase 5, tres por semana |
| Fim da semana 4 | Primeira leitura de numero |

Nao antecipe essa leitura. Antes de umas doze publicacoes o que se ve e ruido, e
mexer no conteudo por causa de ruido e como se perde uma linha editorial que
ainda nem comecou.

---

## O que medir, e o que ignorar

**Mede:**

- Cliques no link do perfil. As tres contas profissionais mostram.
- Cadastros no papiro.work na janela de 24h depois de cada post.
- Salvamentos e compartilhamentos. Em conteudo juridico eles valem mais que
  curtida: quem salva e quem esta vivendo o problema.

**Ignora:**

- Visualizacao isolada de um video que estourou. Um pico nao e tendencia.
- Numero de seguidores nas primeiras semanas.
- Comentario hostil de quem se sente acusado. O produto e do lado de quem
  trabalha; isso atrai contrariedade, e comprar briga custa alcance.

---

## Erros que custam a conta

- **Prometer resultado de processo** em legenda escrita a mao. O gerador tem
  teste que impede isso no conteudo dele; no que voce digitar no aplicativo,
  nao. "Ganhe sua causa", "receba o que e seu", "garanta suas horas" — nenhuma.
- **Se apresentar como escritorio, ou dar consulta no comentario.** Nao ha
  advogado no produto. Resposta padrao para "meu caso e assim, tenho direito?":
  dizer o que a lei diz em geral, dizer que o Papiro registra a prova, e
  recomendar advogado. Nunca opinar sobre o caso.
- **Print de conversa real, holerite real, nome de empresa real.** Nem
  anonimizado, nem "um usuario nosso". Usar conteudo de usuario para vender um
  cofre cifrado contradiz o proprio argumento de venda, alem de ferir o art. 6,
  I da LGPD.
- **Seguir e deixar de seguir em massa** para ganhar seguidor. As tres
  plataformas limitam a conta por isso, e conta nova e a que apanha primeiro.
- **Republicar o mesmo argumento sem perceber.** E para isso que o
  `historico.json` existe, e por isso ele fica versionado no repositorio: em
  disco local ele se perde na primeira troca de maquina.

---

## Se algo travar

| Sintoma | Causa quase sempre |
|---|---|
| `tese desconhecida` | erro de digitacao no `--tese=`. Os ids estao em `scripts/social/banco.mjs` |
| Sai `.webp` em vez de `.mp4` | ffmpeg fora do PATH. Abra um terminal novo; se persistir, `winget install Gyan.FFmpeg` |
| O video parece cortado no aplicativo | nao e o arquivo. TikTok e Reels desenham a interface por cima, e os quadros ja sao desenhados com margem para isso |
| A mesma peca saiu de novo | o lote anterior nao foi registrado. Rode `--registrar` com a semente daquele lote |
