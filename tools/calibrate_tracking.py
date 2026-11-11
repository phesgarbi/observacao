"""Calibra as câmeras master do tracking em ~3 minutos.

Antes: meça com trena a posição de cada câmera master (cm), no sistema de
screens.json: origem no chão, no centro da base da TV 32"; x para a direita de
quem observa, y para cima, z para a frente (na direção da pessoa).
Marque no chão, com fita, dois pontos bem na frente do centro da TV 32":
a 100 cm e a 200 cm da tela.

ILHA (desde 11/11): calibre um lado por vez. Marque com fita, no chão do lado
escolhido, dois pontos em linha reta saindo do meio da mesa: a 60 cm e a 150 cm
da BORDA da mesa (mude com --marcas). Fique nas marcas olhando para as telas
daquele lado. Só as câmeras de tracking que olham para esse lado são calibradas.
    python tools/calibrate_tracking.py --lado esq --olhos 160
    python tools/calibrate_tracking.py --lado dir --olhos 160
As posições das câmeras vêm de config/ilha.json (tools/configurar_ilha.py).

Uso (montagem em U, antiga):
    python tools/calibrate_tracking.py --perfil casa --olhos 160
        --olhos = altura dos olhos de quem vai ficar nas marcas, em cm
    python tools/calibrate_tracking.py --olhos 160 --marcas 100 200 --x 0

Passos na janela:
    1. digite no terminal a posição de cada master que ainda não tiver;
    2. fique na marca de 100 cm, olhando para a TV central, e aperte 1;
    3. fique na marca de 200 cm e aperte 2;
    4. aperte s para salvar (ou q para sair sem salvar).
Calcula para cada master: k_face (escala da distância), yaw e pitch.
"""
import argparse
import math
import time

import cv2
import numpy as np

import _caminho  # noqa: F401
from observacao import config
from observacao.capture import Captura
from observacao.tracker import LARGURA_ROSTO_CM, ModeloCamera, Tracker, base_camera


def coletar(tracker, masters, segundos=2.0):
    """Mediana de (u, v, largura) do maior rosto de cada master."""
    amostras = {m.nome: [] for m in masters}
    t0 = time.time()
    while time.time() - t0 < segundos:
        for m in masters:
            f, idade = m.quadro()
            if f is None or idade > 1:
                continue
            boas, ruins = tracker.detectar(m.nome, f)
            todas = boas + [r for r in ruins]
            if todas:
                d = max(todas, key=lambda d: d.largura_px)
                amostras[m.nome].append((d.olhos_px[0], d.olhos_px[1], d.largura_px))
        time.sleep(0.03)
    return {k: np.median(np.array(v), axis=0) if len(v) >= 5 else None for k, v in amostras.items()}


def resolver(mod, medidas, pontos):
    """k_face, yaw, pitch que melhor explicam as medidas nas marcas."""
    k = mod.k_face
    for _ in range(4):
        fx = k / LARGURA_ROSTO_CM
        ks = []
        for (u, v, w), P in zip(medidas, pontos):
            alcance = np.linalg.norm(P - mod.pos)
            tan2 = ((u - mod.cx) ** 2 + (v - mod.cy) ** 2) / fx ** 2
            ks.append(w * alcance / math.sqrt(1 + tan2))
        k = float(np.mean(ks))
    fx = k / LARGURA_ROSTO_CM
    raios = [np.array([(u - mod.cx) / fx, -(v - mod.cy) / fx, 1.0]) for u, v, w in medidas]
    esperados = [(P - mod.pos) / np.linalg.norm(P - mod.pos) for P in pontos]

    def erro(yaw, pitch):
        r, up, f = base_camera(yaw, pitch)
        e = 0.0
        for d, alvo in zip(raios, esperados):
            dw = d[0] * r + d[1] * up + d[2] * f
            dw /= np.linalg.norm(dw)
            e += 1 - float(dw @ alvo)
        return e

    melhor = (1e9, 0, 0)
    for yaw in np.arange(-180, 180.1, 2):
        for pitch in np.arange(-45, 60.1, 2):
            e = erro(yaw, pitch)
            if e < melhor[0]:
                melhor = (e, yaw, pitch)
    _, y0, p0 = melhor
    for yaw in np.arange(y0 - 2, y0 + 2.01, 0.1):
        for pitch in np.arange(p0 - 2, p0 + 2.01, 0.1):
            e = erro(yaw, pitch)
            if e < melhor[0]:
                melhor = (e, yaw, pitch)
    e, yaw, pitch = melhor
    erro_graus = math.degrees(math.acos(max(-1, 1 - e / len(medidas))))
    return k, float(round(yaw, 1)), float(round(pitch, 1)), erro_graus


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--perfil", default="ilha")
    ap.add_argument("--lado", choices=["esq", "dir"], help="ilha: qual lado calibrar")
    ap.add_argument("--olhos", type=float, required=True, help="altura dos olhos de quem calibra, cm")
    ap.add_argument("--marcas", type=float, nargs=2, default=None,
                    help="distâncias das marcas (ilha: da borda da mesa, padrão 60 150; U: da TV, padrão 100 200)")
    ap.add_argument("--x", type=float, default=0.0, help="U: x das marcas (0 = centro da TV 32)")
    ap.add_argument("--z", type=float, default=0.0, help="ilha: posição das marcas ao longo da mesa (0 = meio)")
    a = ap.parse_args()

    cfg = config.carregar(a.perfil)
    il = cfg.get("ilha")
    if il is not None and not a.lado:
        ap.error("na ilha, diga qual lado: --lado esq ou --lado dir")
    a.marcas = a.marcas or ([60, 150] if il is not None else [100, 200])
    entradas = {c["nome"]: c for c in cfg["cameras"]["cameras"]}
    cap = Captura(cfg)
    masters = cap.por_papel("master")
    if il is not None:
        do_lado = {m["nome"] for m in il.masters if m["olha"] == a.lado}
        masters = [m for m in masters if m.nome in do_lado]
    if not masters:
        print("Nenhuma câmera de tracking (papel master) para calibrar em cameras.json.")
        return
    for m in masters:
        e = entradas[m.nome]
        if not e.get("posicao_cm"):
            txt = input(f"Posição de {m.nome} em cm, 'x y z' (ex.: 0 165 5): ").split()
            e["posicao_cm"] = [float(t) for t in txt]
    cap.cameras = masters
    cap.rodizios = []
    cap.iniciar()
    tracker = Tracker(cfg, masters)
    if il is not None:
        s = -1 if a.lado == "esq" else 1
        pontos = [np.array([s * (il.meia + d), a.olhos, a.z]) for d in a.marcas]
    else:
        pontos = [np.array([a.x, a.olhos, a.marcas[0]]), np.array([a.x, a.olhos, a.marcas[1]])]
    medidas = {1: None, 2: None}
    resultado = {}
    cv2.namedWindow("calibrar tracking", cv2.WINDOW_NORMAL)
    try:
        while True:
            telas = []
            for m in masters:
                f, _ = m.quadro()
                if f is None:
                    f = np.zeros((480, 640, 3), np.uint8)
                f = f.copy()
                boas, ruins = tracker.detectar(m.nome, f)
                for d in boas + ruins:
                    x, y, w, h = d.caixa
                    cv2.rectangle(f, (x, y), (x + w, y + h), (0, 255, 0), 2)
                    cv2.putText(f, f"{w}px", (x, y - 6), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 0), 2)
                cv2.putText(f, m.nome, (10, 28), cv2.FONT_HERSHEY_SIMPLEX, 0.9, (255, 255, 255), 2)
                telas.append(cv2.resize(f, (640, 480)))
            img = np.hstack(telas)
            info = [f"1 = estou em {a.marcas[0]:.0f} cm  {'OK' if medidas[1] else '-'}",
                    f"2 = estou em {a.marcas[1]:.0f} cm  {'OK' if medidas[2] else '-'}",
                    "s = salvar   q = sair"]
            for nome, (k, yaw, pitch, err) in resultado.items():
                info.append(f"{nome}: k={k:.0f} yaw={yaw} pitch={pitch} erro={err:.1f} graus")
            for i, t in enumerate(info):
                cv2.putText(img, t, (10, 470 - 24 * (len(info) - 1 - i)), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 255), 2)
            cv2.imshow("calibrar tracking", img)
            k = cv2.waitKey(30) & 0xFF
            if k in (ord("1"), ord("2")):
                i = 1 if k == ord("1") else 2
                print(f"Coletando na marca {a.marcas[i - 1]:.0f} cm (2 s, fique parado)...")
                medidas[i] = coletar(tracker, masters)
                faltou = [n for n, v in medidas[i].items() if v is None]
                if faltou:
                    print(f"  sem rosto em: {', '.join(faltou)}. Olhe para as telas e repita.")
                if medidas[1] and medidas[2]:
                    resultado = {}
                    for m in masters:
                        if medidas[1].get(m.nome) is None or medidas[2].get(m.nome) is None:
                            continue
                        mod = ModeloCamera(entradas[m.nome])
                        resultado[m.nome] = resolver(mod, [medidas[1][m.nome], medidas[2][m.nome]], pontos)
                        kf, yaw, pitch, err = resultado[m.nome]
                        print(f"  {m.nome}: k_face={kf:.0f}  yaw={yaw}  pitch={pitch}  erro={err:.1f} graus")
            elif k == ord("s"):
                for nome, (kf, yaw, pitch, err) in resultado.items():
                    entradas[nome].update({"k_face": round(kf, 1), "yaw": yaw, "pitch": pitch, "calibrado": True})
                config.salvar_cameras(cfg)
                print(f"Salvo em {cfg['cameras_path']}")
                break
            elif k in (ord("q"), 27):
                break
    finally:
        cap.parar()
        cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
