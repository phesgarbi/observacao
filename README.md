# OBSERVAÇÃO (nome provisório)

> **Desde 11/11 a montagem é em ILHA, não mais em U.** Como funciona e como configurar no local: [docs/ilha.md](docs/ilha.md).
> Resumo: `tools/configurar_ilha.py` (medidas) → `tools/calibrate_tracking.py --lado esq|dir` → `python -m observacao.render.app --mouse`.

Instalação artística sobre vigilância: 5 telas em U, 15 webcams em varas como
"olhos", espaço 3D que acompanha a cabeça de quem observa e mostra, fragmentada,
a pessoa que observou antes. Plano completo em `docs/plano.md`.

## Preparar o ambiente (Windows)

```bat
py -3.10 -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
```

## Ferramentas

| Comando | O que faz |
|---|---|
| `.venv\Scripts\python tools\list_devices.py` | lista câmeras com o caminho USB estável (mesma ordem do OpenCV) |
| `.venv\Scripts\python tools\list_devices_wmi.py` | lista todas as câmeras que o Windows já viu, inclusive desconectadas |

## Pastas

- `docs/` pesquisa, compras, posicionamento, moodboard, identidade, montagem
- `config/` telas, câmeras, frases, perfis (`casa`, `casa_amigo`, `evento`)
- `src/observacao/` código da obra
- `tools/` calibração e diagnóstico
- `bank/` dados gravados na noite (fora do git)

## Documentos

- Plano: `docs/plano.md`
- Pesquisa: síntese e conclusões em `docs/pesquisa/00_sintese.md`; dossiês `01` a `07` (resumo integral de cada fonte + ensaio + conclusões + frases candidatas). `frases_extraidas.md` é a versão antiga, descartada.
- Compras com preços: `docs/compras.md` (v2, a partir do que existe em casa; v1 em `compras_v1_28-10.md`)
- USB: mapa real da placa-mãe e distribuição das 15 câmeras: `docs/usb.md`
- Pesquisa técnica (USB, off-axis, render, modelos): `docs/pesquisa_tecnica.md`
- Resumo compartilhável (Claude Docs): https://claude.ai/artifact/Wby1vyNCtxG3qnHrV7QgTd

## Fase 1: captura (05/11)

Com as webcams plugadas, nesta ordem:

```
.venv/Scripts/python tools/list_devices.py
.venv/Scripts/python tools/calibrate_cameras.py
.venv/Scripts/python tools/bench_usb.py
cd src && ../.venv/Scripts/python -m observacao.operator
```

Sem webcams: `cd src && ../.venv/Scripts/python -m observacao.operator --fake 15`.
Plano dos 9 dias: `docs/plano_9_dias.md`.

Telas e video wall: `docs/video.md` e `tools/test_screens.py`.

Notebook como servidor de câmeras: `docs/notebook.md` e `servidor_notebook.bat`.

## Tracking da cabeça (05/11)

Detector YuNet do OpenCV (`models/face_detection_yunet_2023mar.onnx`), posição 3D dos olhos de até 3 pessoas.

```
.venv/Scripts/python tools/calibrate_tracking.py --olhos 160
.venv/Scripts/python tools/tracking_teste.py
```

Ao ligar, espere ~25 s: é o tempo de o filtro aprender os rostos parados (estampas, cartazes).

## Render: telas como janelas (05/11)

Pré-visualização das 5 telas numa janela (um monitor basta):

```
cd src && ../.venv/Scripts/python -m observacao.render.app --mouse
cd src && ../.venv/Scripts/python -m observacao.render.app
```

`--mouse`: a cabeça segue o mouse (roda = distância). Sem `--mouse`: segue a pessoa do tracking; sem ninguém, deriva sozinha.
Espaço alterna mouse/tracking, G grade, H texto, Esc sai. `--telas` abre a janela das telas físicas (montagem real, depois de ligar as 5 telas). `--fake 8` usa câmeras simuladas.


## Pessoas, banco e frases (dia 4)

O app de render já faz a obra inteira na pré-visualização:

- Cada pessoa confirmada pelo tracking abre uma sessão em `bank/` (um quadro de cada câmera a cada 0,5 s). Quando ela sai, a sessão fecha e vira "a anterior".
- 1 pessoa vê a anterior; 2 se veem; 3 em anel (da esquerda para a direita). Cada tela é de quem está mais de frente para ela; tela de ninguém mostra frase grande.
- 4 ou mais: sem 3D, mural de todo mundo que passou com frases enormes. Volta ao 3D 3 s depois de ficarem 3 ou menos.
- Frases provisórias em `config/frases/provisorias.txt` (candidatas dos dossiês) + frases descritivas sobre quem está ali.

Teclas no app: `0` a `5` simulam essa quantidade de pessoas; `Espaço` alterna mouse/tracking.
Para limpar o banco: apagar a pasta `bank/`. Com `--fake N` o banco vai para `bank_teste/`.

### Textos: desligados por enquanto (06/11)

Os letreiros 2D (frase grande, legenda embaixo, faixa sobre o mural) foram desligados em `config/app.json` (`"frases": {"ativas": false}`). Telas sem dono passam a mostrar o 3D visto pela pessoa mais perto.

Direção para quando voltarem: **o texto também é espacial**. Ele mora dentro do espaço 3D, em profundidade, com a mesma paralaxe off-axis dos cacos, em vez de ficar colado na superfície da tela. `render/text.py` e `phrases.py` ficam no código como base.

### Medir a montagem com fotos

Posição, altura e ângulo de cada tela saem de fotos de celular. No app, a tecla `M` (ou a opção `--marcadores`) mostra marcadores ArUco nas telas; depois `tools/fotos_telas.py <pasta>` calcula tudo e, com `--escrever`, grava no screens.json. O roteiro completo das fotos, de medição e de documentação, está em [docs/fotos_medicao.md](docs/fotos_medicao.md).
