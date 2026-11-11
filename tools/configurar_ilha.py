"""Configura a montagem em ILHA no local: pergunta as medidas uma a uma e salva config/ilha.json.

Uso (na pasta do projeto):
    .venv/Scripts/python tools/configurar_ilha.py            pergunta tudo (Enter mantém o valor atual)
    .venv/Scripts/python tools/configurar_ilha.py --so mesa  só um grupo: mesa, telas, tracking, zonas, comportamento
    .venv/Scripts/python tools/configurar_ilha.py --set mesa.altura_cm=78 --set tracking.M1.altura_cm=165
    .venv/Scripts/python tools/configurar_ilha.py --ver      só mostra o mapa e o resumo

Chaves de --set: grupo.campo, ou grupo.ID.campo para telas (tubo_esq, esq_21, tubo_dir,
dir_21_touch, centro_32) e câmeras de tracking (M1..M4). "auto" deixa yaw/pitch automáticos.

Todas as medidas em cm, com trena:
    mesa          comprimento (ao longo da vara), largura, altura do tampo.
    telas         ao_longo_cm: da PONTA DA FRENTE da mesa (lado da 32") até o centro da tela;
                  recuo_cm: quanto a face da tela fica para dentro da borda da mesa (0 = rente);
                  centro_altura_cm: do chão até o centro da IMAGEM; largura/altura: só a área de imagem.
                  32": distancia_cm = da ponta da frente da mesa até a tela.
    tracking      altura_cm da câmera; afastamento_cm = distância da ponta (frente/fundo) da mesa.
    zonas         perto_cm = da borda da mesa até a FITA no chão; longe_cm = até onde registra;
                  frente_cm / fundo_cm = até onde vai a área, a partir do CENTRO da mesa.
Depois: rode calibrate_tracking.py para refinar as câmeras e confira no app com --mouse.
"""
import argparse
import sys

import _caminho  # noqa: F401
import cv2

from observacao import ilha as IL
from observacao.config import RAIZ

PERGUNTAS = {
    "mesa": [("comprimento_cm", "comprimento da mesa (ao longo da vara)"),
             ("largura_cm", "largura da mesa"),
             ("altura_cm", "altura do tampo")],
    "telas_lado": [("ao_longo_cm", "da ponta da FRENTE da mesa até o centro da tela"),
                   ("recuo_cm", "recuo da face da tela para dentro da borda da mesa (0 = rente)"),
                   ("centro_altura_cm", "do chão até o centro da imagem"),
                   ("largura_cm", "largura da área de imagem"),
                   ("altura_cm", "altura da área de imagem")],
    "telas_frente": [("distancia_cm", "da ponta da frente da mesa até a tela"),
                     ("lateral_cm", "deslocamento para o lado (+ = direita de quem chega, 0 = no eixo)"),
                     ("centro_altura_cm", "do chão até o centro da imagem"),
                     ("largura_cm", "largura da área de imagem"),
                     ("altura_cm", "altura da área de imagem")],
    "tracking": [("altura_cm", "altura da câmera"),
                 ("afastamento_cm", "distância da ponta da mesa (+ para fora)"),
                 ("yaw", "giro em graus (auto = reto para o lado)"),
                 ("pitch", "inclinação para baixo em graus (auto = mira os olhos na zona perto)")],
    "zonas": [("perto_cm", "da borda da mesa até a FITA (zona perto)"),
              ("longe_cm", "da borda da mesa até onde registra (zona longe)"),
              ("frente_cm", "do centro da mesa até onde vai a área, para a frente"),
              ("fundo_cm", "do centro da mesa até onde vai a área, para o fundo")],
    "comportamento": [("ver_se_s", "segundos em que o dono se vê ao vivo"),
                      ("troca_s", "a cada quantos segundos troca a pessoa mostrada"),
                      ("alerta_s", "segundos até o alerta de consumo excessivo"),
                      ("sair_s", "segundos fora da zona perto até fechar a sessão"),
                      ("lotacao", "pessoas na zona perto para o lado virar mural"),
                      ("troca_dono_cm", "quanto mais perto outra pessoa precisa estar para tomar a tela")],
}


def _valor(txt):
    t = txt.strip().replace(",", ".")
    if t.lower() in ("auto", "null", "none"):
        return None
    f = float(t)
    return int(f) if f.is_integer() else f


def _alvo(dados, chave):
    """'mesa.altura_cm' ou 'telas.tubo_esq.ao_longo_cm' -> (dicionário, campo)."""
    partes = chave.split(".")
    if partes[0] in ("telas", "tracking") and len(partes) == 3:
        campo_id = "id" if partes[0] == "telas" else "nome"
        item = next((i for i in dados[partes[0]] if i[campo_id] == partes[1]), None)
        if item is None:
            raise KeyError(f"não existe {partes[0]} '{partes[1]}'")
        return item, partes[2]
    if len(partes) == 2 and isinstance(dados.get(partes[0]), dict):
        return dados[partes[0]], partes[1]
    raise KeyError(f"chave inválida: {chave}")


def perguntar(d, campo, texto):
    atual = d.get(campo)
    while True:
        r = input(f"  {texto} [{'auto' if atual is None else atual}]: ").strip()
        if not r:
            return
        try:
            d[campo] = _valor(r)
            return
        except ValueError:
            print("    número, por favor (ou Enter para manter, 'auto' para automático)")


def interativo(dados, so=None):
    grupos = [so] if so else ["mesa", "telas", "tracking", "zonas", "comportamento"]
    for g in grupos:
        print(f"\n== {g.upper()} ==")
        if g == "telas":
            for t in dados["telas"]:
                print(f"\n {t['id']}  (lado {t['lado']}, {t['tipo']})")
                for campo, texto in PERGUNTAS["telas_frente" if t["lado"] == "frente" else "telas_lado"]:
                    perguntar(t, campo, texto)
        elif g == "tracking":
            for c in dados["tracking"]:
                print(f"\n {c['nome']}  (ponta {c['ponta']}, olha para {c['olha']})")
                for campo, texto in PERGUNTAS["tracking"]:
                    perguntar(c, campo, texto)
        else:
            for campo, texto in PERGUNTAS[g]:
                perguntar(dados[g], campo, texto)


def resumo(il):
    print("\nTelas (cm; x direita de quem chega, y altura, z para a frente; yaw = para onde olha):")
    for t in il.telas:
        x, y, z = t["centro_cm"]
        print(f"  {t['id']:<14} lado {t['lado']:<6} centro ({x:6.1f}, {y:6.1f}, {z:6.1f})  yaw {t['yaw']:6.1f}")
    print("Câmeras de tracking:")
    for m in il.masters:
        x, y, z = m["posicao_cm"]
        print(f"  {m['nome']:<4} olha {m['olha']:<4} pos ({x:6.1f}, {y:6.1f}, {z:6.1f})  yaw {m['yaw']:6.1f}  pitch {m['pitch']:5.1f}")
    print(f"Zonas: perto até {il.perto:.0f} cm da mesa (fita), longe até {il.longe:.0f} cm; "
          f"ao longo da mesa de {il.zona_z[0]:.0f} a {il.zona_z[1]:.0f} cm.")


def avisos(il):
    """Medidas que não fazem sentido juntas (não impede salvar, só avisa)."""
    out = []
    if il.longe <= il.perto:
        out.append(f"zona longe ({il.longe:.0f}) menor ou igual à perto ({il.perto:.0f}): longe deve ser maior.")
    for lado in IL.LADOS:
        ts = sorted(il.telas_de(lado), key=lambda t: t["centro_cm"][2])
        for t in ts:
            z = t["centro_cm"][2]
            if not (il.z_fundo - 5 <= z - t["largura_cm"] / 2 and z + t["largura_cm"] / 2 <= il.z_frente + 5):
                out.append(f"{t['id']} passa da ponta da mesa (confira ao_longo_cm e a largura).")
        for a_, b_ in zip(ts, ts[1:]):
            if a_["centro_cm"][2] + a_["largura_cm"] / 2 > b_["centro_cm"][2] - b_["largura_cm"] / 2:
                out.append(f"{a_['id']} e {b_['id']} se sobrepõem ao longo da mesa.")
    try:
        import json
        cams = json.loads((RAIZ / "config" / "cameras.json").read_text(encoding="utf-8")).get("cameras", [])
        nomes = {c.get("nome") for c in cams}
        faltam = [m["nome"] for m in il.masters if m["nome"] not in nomes]
        calibradas = [c["nome"] for c in cams if c.get("calibrado") and c.get("nome") in {m["nome"] for m in il.masters}]
        if calibradas:
            out.append(f"já calibradas no local: {', '.join(calibradas)}. Posição e ângulo delas vêm da calibração; "
                       "se mexeu nelas, rode calibrate_tracking.py de novo.")
        if faltam:
            out.append(f"câmeras de tracking sem cadastro em cameras.json: {', '.join(faltam)} "
                       "(cadastre com tools/calibrate_cameras.py, papel master).")
    except (OSError, ValueError):
        pass
    if out:
        print("\nATENÇÃO:")
        for o in out:
            print("  - " + o)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--arquivo", default="config/ilha.json")
    ap.add_argument("--so", choices=["mesa", "telas", "tracking", "zonas", "comportamento"])
    ap.add_argument("--set", action="append", default=[], metavar="CHAVE=VALOR")
    ap.add_argument("--ver", action="store_true", help="só mostra o mapa e o resumo")
    ap.add_argument("--sem-janela", action="store_true", help="não abre a janela do mapa (só salva o PNG)")
    a = ap.parse_args()

    caminho = RAIZ / a.arquivo
    dados = IL.carregar(caminho)
    if a.set:
        for kv in a.set:
            chave, _, v = kv.partition("=")
            try:
                d, campo = _alvo(dados, chave.strip())
                d[campo] = _valor(v)
            except (KeyError, ValueError) as e:
                raise SystemExit(f"--set {kv}: {e}. Nada foi salvo.")
            print(f"{chave} = {d[campo]}")
    elif not a.ver:
        print(f"Medidas da ilha ({caminho}). Enter mantém o valor entre colchetes.")
        interativo(dados, a.so)
    if not a.ver:
        IL.salvar(dados, caminho)
        print(f"\nsalvo em {caminho}")

    il = IL.Ilha(dados)
    resumo(il)
    avisos(il)
    img = IL.mapa(il, largura_px=700)
    png = caminho.with_name("ilha_mapa.png")
    cv2.imwrite(str(png), img)
    print(f"mapa em {png}")
    if not a.sem_janela:
        try:
            cv2.imshow("ilha - vista de cima (qualquer tecla fecha)", img)
            cv2.waitKey(0)
            cv2.destroyAllWindows()
        except cv2.error:
            pass


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    main()
