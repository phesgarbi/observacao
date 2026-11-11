"""Maquete 3D da instalação OBSERVAÇÃO em ilha, gerada a partir das MESMAS medidas
do programa (config/ilha.json, via src/observacao/ilha.py). Não faz parte da obra:
serve para ver a montagem antes de montar. Rodar na pasta do projeto:

    blender -b -P tools/maquete_ilha_blender.py                 (renders em docs/maquete/)
    blender -b -P tools/maquete_ilha_blender.py -- . saida/     (repo e pasta de saída)

Pessoas, refletores e paredes são ilustrativos (paredes: fundo liso, janela à esquerda,
cortinas à direita). Texturas das telas: docs/maquete/tex/*.png, se existirem.

Gera cena_ilha.blend e os renders. Referencial do programa: cm, x para a direita
de quem chega, y para cima, z para a frente (entrada). No Blender: X = x,
Y = -z, Z = y, em metros.
"""
import math
import sys
from pathlib import Path

import bpy
from mathutils import Vector

argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
REPO = Path(argv[0]).resolve() if argv else Path(__file__).resolve().parents[1]
SAIDA = Path(argv[1]).resolve() if len(argv) > 1 else REPO / "docs" / "maquete"
TEX = REPO / "docs" / "maquete" / "tex"
sys.path.insert(0, str(REPO / "src"))
from observacao import ilha as IL  # noqa: E402

ILHA = IL.Ilha(IL.carregar(REPO / "config" / "ilha.json"))
TOPO = ILHA.dados["mesa"]["altura_cm"] / 100
PAREDE_Z = ILHA.z_fundo - 25                  # mesa quase encostada na parede do fundo
X_SALA = ILHA.meia + ILHA.longe + 40          # paredes laterais logo depois da zona longe


def B(x, y, z):
    return Vector((x / 100, -z / 100, y / 100))


def dirB(dx, dy, dz):
    return Vector((dx, -dz, dy)).normalized()


bpy.ops.wm.read_factory_settings(use_empty=True)
cena = bpy.context.scene
SO_PLANTA, SO_3D = [], []                     # objetos que só aparecem numa das vistas


# ------------------------------------------------------------------ materiais
def mat(nome, cor, rug=0.6, metal=0.0, emissao=0.0, cor_em=None, alfa=1.0, coat=0.0):
    m = bpy.data.materials.new(nome)
    m.use_nodes = True
    p = m.node_tree.nodes["Principled BSDF"]
    p.inputs["Base Color"].default_value = (*cor, 1)
    p.inputs["Roughness"].default_value = rug
    p.inputs["Metallic"].default_value = metal
    if coat and "Coat Weight" in p.inputs:
        p.inputs["Coat Weight"].default_value = coat
    if emissao:
        p.inputs["Emission Color"].default_value = (*(cor_em or cor), 1)
        p.inputs["Emission Strength"].default_value = emissao
    if alfa < 1:
        p.inputs["Alpha"].default_value = alfa
        if hasattr(m, "surface_render_method"):
            m.surface_render_method = "BLENDED"
    return m


def mat_tela(nome, img, forca=3.5):
    m = bpy.data.materials.new(nome)
    m.use_nodes = True
    nt = m.node_tree
    for n in list(nt.nodes):
        nt.nodes.remove(n)
    out = nt.nodes.new("ShaderNodeOutputMaterial")
    em = nt.nodes.new("ShaderNodeEmission")
    em.inputs["Strength"].default_value = forca
    if img.exists():
        tx = nt.nodes.new("ShaderNodeTexImage")
        tx.image = bpy.data.images.load(str(img))
        nt.links.new(tx.outputs["Color"], em.inputs["Color"])
    nt.links.new(em.outputs["Emission"], out.inputs["Surface"])
    return m


def mat_volume(densidade):
    m = bpy.data.materials.new("nevoa")
    m.use_nodes = True
    nt = m.node_tree
    for n in list(nt.nodes):
        nt.nodes.remove(n)
    out = nt.nodes.new("ShaderNodeOutputMaterial")
    v = nt.nodes.new("ShaderNodeVolumePrincipled")
    v.inputs["Density"].default_value = densidade
    nt.links.new(v.outputs["Volume"], out.inputs["Volume"])
    return m


M = {
    "chao": mat("chao", (0.035, 0.034, 0.033), rug=0.32, coat=0.2),
    "parede": mat("parede", (0.32, 0.30, 0.28), rug=0.85),
    "cortina": mat("cortina", (0.09, 0.02, 0.025), rug=0.95),
    "caixilho": mat("caixilho", (0.05, 0.05, 0.05), rug=0.4, metal=0.5),
    "vidro": mat("vidro", (0.05, 0.08, 0.14), rug=0.1, emissao=0.6, cor_em=(0.12, 0.2, 0.4)),
    "tampo": mat("tampo", (0.52, 0.38, 0.25), rug=0.55),
    "cavalete": mat("cavalete", (0.42, 0.3, 0.2), rug=0.7),
    "crt": mat("crt", (0.62, 0.6, 0.55), rug=0.45),
    "preto": mat("preto", (0.015, 0.015, 0.017), rug=0.35),
    "metal": mat("metal", (0.08, 0.08, 0.09), rug=0.3, metal=0.9),
    "webcam": mat("webcam", (0.02, 0.02, 0.022), rug=0.3),
    "led_track": mat("led_track", (1, 0.1, 0.05), emissao=8),
    "led_foto": mat("led_foto", (0.2, 1, 0.3), emissao=6),
    "fita": mat("fita", (0.95, 0.75, 0.05), rug=0.6, emissao=0.25),
    "pessoa": mat("pessoa", (0.42, 0.42, 0.44), rug=0.75),
    "dono": mat("dono", (0.25, 0.5, 0.75), rug=0.6, emissao=0.15),
    "z_perto": mat("z_perto", (0.15, 0.6, 0.3), emissao=0.4, alfa=0.45),
    "z_longe": mat("z_longe", (0.12, 0.3, 0.2), emissao=0.2, alfa=0.35),
    "cone": mat("cone", (1.0, 0.25, 0.15), emissao=0.6, alfa=0.06),
    "texto": mat("texto", (1, 1, 1), emissao=2.5),
    "strobo": mat("strobo", (1, 0.06, 0.04), emissao=12),
}


# ------------------------------------------------------------------ primitivas
def _fin(o, material, bevel=0.0, lista=None):
    o.data.materials.append(material)
    if bevel:
        b = o.modifiers.new("bevel", "BEVEL")
        b.width, b.segments = bevel, 3
    if lista is not None:
        lista.append(o)
    return o


def caixa(nome, c, dims, material, rot_z=0.0, bevel=0.006, lista=None):
    bpy.ops.mesh.primitive_cube_add(size=1, location=c)
    o = bpy.context.object
    o.name, o.scale, o.rotation_euler = nome, dims, (0, 0, rot_z)
    bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
    return _fin(o, material, bevel, lista)


def cilindro(nome, a, b, raio, material, verts=20, lista=None, raio2=None):
    a, b = Vector(a), Vector(b)
    d = b - a
    if raio2 is None:
        bpy.ops.mesh.primitive_cylinder_add(vertices=verts, radius=raio, depth=d.length, location=(a + b) / 2)
    else:
        bpy.ops.mesh.primitive_cone_add(vertices=verts, radius1=raio, radius2=raio2, depth=d.length,
                                        location=(a + b) / 2)
    o = bpy.context.object
    o.name = nome
    o.rotation_mode = "QUATERNION"
    o.rotation_quaternion = d.to_track_quat("Z", "Y")
    bpy.ops.object.shade_smooth()
    return _fin(o, material, lista=lista)


def esfera(nome, c, r, material, escala=(1, 1, 1), lista=None):
    bpy.ops.mesh.primitive_uv_sphere_add(radius=r, location=c, segments=32, ring_count=16)
    o = bpy.context.object
    o.name, o.scale = nome, escala
    bpy.ops.object.shade_smooth()
    return _fin(o, material, lista=lista)


def malha(nome, verts, faces, material, lista=None):
    me = bpy.data.meshes.new(nome)
    me.from_pydata([tuple(v) for v in verts], [], faces)
    o = bpy.data.objects.new(nome, me)
    bpy.context.collection.objects.link(o)
    return _fin(o, material, lista=lista)


def texto(t, pos, tam=0.16, rot_z=0.0):
    cu = bpy.data.curves.new("txt", "FONT")
    cu.body, cu.size, cu.align_x, cu.align_y = t, tam, "CENTER", "CENTER"
    o = bpy.data.objects.new("txt", cu)
    bpy.context.collection.objects.link(o)
    o.location, o.rotation_euler = pos, (0, 0, rot_z)
    return _fin(o, M["texto"], lista=SO_PLANTA)


def tripe(nome, base, topo, abertura=0.3):
    x, y = base
    for i in range(3):
        a = math.radians(90 + i * 120)
        cilindro(f"{nome}_p{i}", (x + abertura * math.cos(a), y + abertura * math.sin(a), 0), (x, y, topo * 0.5),
                 0.011, M["metal"], 8)
    cilindro(f"{nome}_c", (x, y, topo * 0.5), (x, y, topo), 0.016, M["metal"], 12)


def pessoa(nome, x, z, h, olha_para, material):
    """Manequim simples, em pé, virado para o ponto olha_para (mundo, cm)."""
    pai = bpy.data.objects.new(nome, None)
    bpy.context.collection.objects.link(pai)
    h = h / 100
    partes = []
    for s in (-1, 1):                                                     # pernas e braços
        partes.append(cilindro(f"{nome}_perna", (s * 0.09, 0, 0.0), (s * 0.085, 0, 0.47 * h), 0.065, material,
                               raio2=0.075))
        partes.append(cilindro(f"{nome}_braco", (s * 0.2, 0.0, 0.79 * h), (s * 0.22, -0.04, 0.48 * h), 0.036,
                               material, raio2=0.03, verts=16))
    partes.append(esfera(f"{nome}_quadril", (0, 0, 0.5 * h), 0.17, material, (1, 0.62, 0.55)))
    partes.append(cilindro(f"{nome}_tronco", (0, 0, 0.5 * h), (0, 0, 0.8 * h), 0.15, material, raio2=0.2))
    partes.append(esfera(f"{nome}_ombros", (0, 0, 0.8 * h), 0.2, material, (1, 0.55, 0.32)))
    partes.append(cilindro(f"{nome}_pescoco", (0, 0, 0.8 * h), (0, 0, 0.87 * h), 0.05, material))
    partes.append(esfera(f"{nome}_cabeca", (0, -0.01, 0.93 * h), 0.1, material, (0.85, 0.95, 1.12)))
    for o in partes:
        o.parent = pai
        SO_3D.append(o)
    pai.location = B(x, 0, z)
    d = B(*olha_para) - B(x, 0, z)
    pai.rotation_euler = (0, 0, math.atan2(d.x, -d.y))
    esfera(f"{nome}_planta", B(x, h * 100, z), 0.2, material, (1, 1, 0.3), lista=SO_PLANTA)
    return pai


# ------------------------------------------------------------------ sala
# fundo: parede lisa, mesa quase encostada. Laterais, DE FRENTE para as telas:
# esquerda = parede com janela; direita = cortinas.
caixa("chao", (0, 0.5, -0.01), (14, 14, 0.02), M["chao"], bevel=0)
yp = -PAREDE_Z / 100                                                      # Blender y da parede do fundo
XP = X_SALA / 100                                                         # Blender x das paredes laterais
Y_FRENTE = -(ILHA.zona_z[1] + 120) / 100                                  # até onde as laterais vão
comp_lat = yp - Y_FRENTE
caixa("parede_fundo", (0, yp + 0.06, 1.6), (2 * XP + 0.24, 0.12, 3.2), M["parede"], bevel=0)
caixa("parede_esq", (-XP - 0.06, (yp + Y_FRENTE) / 2, 1.6), (0.12, comp_lat, 3.2), M["parede"], bevel=0)
jy, jw, jh, jz = 0.0, 1.8, 1.2, 1.55                                     # janela na parede esquerda
caixa("janela_vidro", (-XP + 0.005, jy, jz), (0.02, jw, jh), M["vidro"], bevel=0)
for dy in (-jw / 2, 0, jw / 2):
    caixa("caixilho_v", (-XP + 0.02, jy + dy, jz), (0.04, 0.04, jh + 0.04), M["caixilho"], bevel=0.003)
for dz in (-jh / 2, jh / 2):
    caixa("caixilho_h", (-XP + 0.02, jy, jz + dz), (0.04, jw + 0.04, 0.04), M["caixilho"], bevel=0.003)
n_c, alt_c = 160, 3.1                                                     # cortina ondulada na direita
vs, fs = [], []
for i in range(n_c + 1):
    y = Y_FRENTE + i / n_c * comp_lat
    dx = 0.05 * math.sin(i / n_c * comp_lat * 2 * math.pi * 3.2)
    vs += [(XP + dx, y, 0.0), (XP + dx, y, alt_c)]
    if i:
        k = 2 * i
        fs.append((k - 2, k, k + 1, k - 1))
cortina = malha("cortina", vs, fs, M["cortina"])
cortina.modifiers.new("solid", "SOLIDIFY").thickness = 0.01
for pol in cortina.data.polygons:
    pol.use_smooth = True

# ------------------------------------------------------------------ zonas (só planta) e fita no chão (todas)
z0, z1 = ILHA.zona_z
for s in (-1, 1):
    for nome, d0, d1, m_, h in (("longe", ILHA.perto, ILHA.longe, M["z_longe"], 0.003),
                                ("perto", 0, ILHA.perto, M["z_perto"], 0.005)):
        xa, xb = s * (ILHA.meia + d0), s * (ILHA.meia + d1)
        c = B((xa + xb) / 2, 0, (z0 + z1) / 2)
        caixa(f"zona_{nome}{s}", (c.x, c.y, h), (abs(xb - xa) / 100, (z1 - z0) / 100, 0.002), m_, bevel=0,
              lista=SO_PLANTA)
    c = B(s * (ILHA.meia + ILHA.perto), 0, (z0 + z1) / 2)
    caixa(f"fita{s}", (c.x, c.y, 0.002), (0.048, (z1 - z0) / 100, 0.002), M["fita"], bevel=0)

# ------------------------------------------------------------------ mesa de cavaletes
caixa("tampo", (0, 0, TOPO - 0.012), (ILHA.larg / 100, ILHA.comp / 100, 0.024), M["tampo"], bevel=0.004)
for sy in (-1, 1):
    y = sy * (ILHA.comp / 200 - 0.15)
    for sx in (-1, 1):
        for sd in (-1, 1):
            cilindro("cavalete", (sx * (ILHA.larg / 200 - 0.04), y + sd * 0.12, 0),
                     (sx * 0.05, y + sd * 0.02, TOPO - 0.04), 0.017, M["cavalete"], 8)
    caixa("cavalete_trave", (0, y, TOPO - 0.05), (ILHA.larg / 100 - 0.06, 0.05, 0.04), M["cavalete"])

# ------------------------------------------------------------------ telas
TEXTURA = {"tubo_esq": "tubo_esq", "tubo_dir": "tubo_dir", "esq_21": "esq_21", "dir_21_touch": "dir_21",
           "centro_32": "centro_32"}
for t in ILHA.telas:
    yaw = t["yaw"]
    n = dirB(math.sin(math.radians(yaw)), 0, math.cos(math.radians(yaw)))
    rz = math.radians(yaw)
    face = B(*t["centro_cm"])
    w, h = t["largura_cm"] / 100, t["altura_cm"] / 100
    if t["tipo"] == "crt":
        bw, bh, bd = w + 0.08, h + 0.11, 0.30
        c = face - n * (bd / 2)
        c.z = TOPO + bh / 2
        caixa(f"crt_{t['id']}", c, (bw, bd, bh), M["crt"], rz, bevel=0.025)
        caixa(f"crt_moldura_{t['id']}", face - n * 0.009, (w + 0.03, 0.012, h + 0.03), M["preto"], rz, bevel=0.01)
    else:
        moldura = 0.012
        caixa(f"painel_{t['id']}", face - n * 0.016, (w + moldura * 2, 0.025, h + moldura * 2), M["preto"], rz,
              bevel=0.004)
        if t["tipo"] == "monitor":
            base = face - n * 0.07
            cilindro("pe", (base.x, base.y, TOPO), (base.x, base.y, face.z - 0.05), 0.018, M["metal"], 12)
            caixa("base", (base.x, base.y, TOPO + 0.006), (0.24, 0.17, 0.012), M["metal"], rz, bevel=0.004)
        else:   # 32" de pé num tripé
            tripe("tripe_32", (face.x - n.x * 0.05, face.y - n.y * 0.05), face.z - h / 2 + 0.05)
    bpy.ops.mesh.primitive_plane_add(size=1, location=face + n * 0.001)
    p = bpy.context.object
    p.name, p.scale, p.rotation_euler = f"imagem_{t['id']}", (w, h, 1), (math.radians(90), 0, rz)
    p.data.materials.append(mat_tela(f"img_{t['id']}", TEX / f"{TEXTURA[t['id']]}.png"))

# ------------------------------------------------------------------ vara e câmeras
ALTO = 1.74
z_vara = (ILHA.z_frente + 30, ILHA.z_fundo - 5)
for zp in (ILHA.z_frente - 6, ILHA.z_fundo + 6):
    a = B(0, TOPO * 100, zp)
    cilindro("poste", a, (a.x, a.y, ALTO + 0.02), 0.014, M["metal"], 12)
    caixa("sargento", (a.x, a.y, TOPO + 0.02), (0.06, 0.06, 0.04), M["metal"], bevel=0.004)
cilindro("vara", B(0, ALTO * 100, z_vara[0]), B(0, ALTO * 100, z_vara[1]), 0.014, M["metal"], 12)


def webcam(nome, pos, d, led):
    corpo = caixa(nome, pos, (0.08, 0.035, 0.03), M["webcam"], bevel=0.008)
    corpo.rotation_mode = "QUATERNION"
    corpo.rotation_quaternion = d.to_track_quat("Y", "Z")
    cilindro(nome + "_lente", pos + d * 0.017, pos + d * 0.024, 0.011, M["preto"], 16)
    lado = d.cross(Vector((0, 0, 1)))
    if lado.length > 1e-6:
        lado.normalize()
    esfera(nome + "_led", pos + d * 0.019 + Vector((0, 0, 0.011)) + lado * 0.025, 0.004, M[led])
    return corpo


HFOV, VFOV, ALCANCE = 65.0, 48.0, 2.6
for m in ILHA.masters:
    p = B(*m["posicao_cm"])
    yaw, pitch = math.radians(m["yaw"]), math.radians(m["pitch"])
    d = dirB(math.sin(yaw) * math.cos(pitch), -math.sin(pitch), math.cos(yaw) * math.cos(pitch))
    webcam(m["nome"], p + d * 0.04, d, "led_track")
    r = d.cross(Vector((0, 0, 1))).normalized()
    u = r.cross(d).normalized()
    th, tv = math.tan(math.radians(HFOV / 2)), math.tan(math.radians(VFOV / 2))
    cantos = [p + (d + r * sx * th + u * sy * tv) * ALCANCE for sx, sy in ((-1, -1), (1, -1), (1, 1), (-1, 1))]
    malha(f"cone_{m['nome']}", [p, *cantos], [(0, 1, 2), (0, 2, 3), (0, 3, 4), (0, 4, 1)], M["cone"],
          lista=SO_PLANTA)
    texto(m["nome"], (p.x + d.x * 0.45, p.y + d.y * 0.45, 2.0), 0.14)

# câmeras de foto: numa fila só, sob a vara, alternando esquerda / direita (entre as de tracking das pontas)
N_FOTO = 10
for i in range(N_FOTO):
    zf = 50 - i * 100 / (N_FOTO - 1)
    s = -1 if i % 2 == 0 else 1
    webcam(f"foto{i}", B(s * 2, (ALTO - 0.06) * 100, zf), dirB(s, -0.3, 0), "led_foto")

# ------------------------------------------------------------------ refletores PAR: no chão, debaixo da borda da
# mesa, um por lado, de baixo para cima mirando o rosto de quem está na zona perto
for s in (-1, 1):
    pos = B(s * (ILHA.meia - 8), 14, 0)
    alvo = B(s * (ILHA.meia + 75), 165, 0)
    d = (alvo - pos).normalized()
    caixa(f"par_base{s}", (pos.x, pos.y, 0.02), (0.24, 0.2, 0.04), M["preto"], bevel=0.008)
    cilindro(f"par{s}", pos - d * 0.1, pos + d * 0.08, 0.1, M["preto"], 24)
    bpy.ops.object.light_add(type="SPOT", location=pos + d * 0.09)
    luz = bpy.context.object
    luz.data.energy, luz.data.spot_size, luz.data.spot_blend = 350, math.radians(45), 0.4
    luz.data.color = (1.0, 0.35, 0.75) if s < 0 else (0.35, 0.6, 1.0)
    luz.rotation_mode = "QUATERNION"
    luz.rotation_quaternion = d.to_track_quat("-Z", "Y")

# ------------------------------------------------------------------ vermelho constante (ideia do Gui, 11/11): fita LED
# vermelha ao longo da vara, acesa o tempo todo, como a luz de "gravando" das câmeras de segurança.
fita_c = (B(0, (ALTO + 0.016) * 100, z_vara[0]) + B(0, (ALTO + 0.016) * 100, z_vara[1])) / 2
fita_len = abs(z_vara[0] - z_vara[1]) / 100
caixa("fita_led", fita_c, (0.012, fita_len, 0.004), M["strobo"], bevel=0)
for k in range(4):
    zf = z_vara[0] + (z_vara[1] - z_vara[0]) * (k + 0.5) / 4
    bpy.ops.object.light_add(type="POINT", location=B(0, (ALTO + 0.05) * 100, zf))
    lz = bpy.context.object
    lz.data.energy, lz.data.color, lz.data.shadow_soft_size = 2.0, (1.0, 0.06, 0.04), 0.05

# ------------------------------------------------------------------ pessoas (x, z, altura, olha para, dono)
PESSOAS = [
    (-ILHA.meia - 70, 0, 172, (-20, 120, 0), True),         # dono do lado esquerdo
    (ILHA.meia + 65, -25, 164, (20, 120, -25), True),       # dono do lado direito
    (-ILHA.meia - 85, -60, 178, (0, 120, -30), False),
    (-ILHA.meia - 210, 130, 168, (0, 120, 0), False),
    (-ILHA.meia - 250, -20, 181, (-60, 120, 200), False),
    (ILHA.meia + 200, 150, 175, (0, 120, 0), False),
    (ILHA.meia + 250, 20, 160, (330, 120, 160), False),
    (ILHA.meia + 160, -45, 170, (0, 120, -30), False),
    (-95, ILHA.z_frente + 230, 170, (0, 120, 0), False),    # chegando
    (120, ILHA.z_frente + 280, 176, (0, 120, 0), False),
]
for i, (x, z, alt, alvo, dono) in enumerate(PESSOAS):
    pessoa(f"pessoa{i}", x, z, alt, alvo, M["dono"] if dono else M["pessoa"])
DONO_ESQ = [o for o in bpy.data.objects if o.name.startswith("pessoa0_") and not o.name.endswith("_planta")]

# ------------------------------------------------------------------ rótulos (só na planta)
ACIMA = Vector((0, 0, 0.02))
texto("ENTRADA", B(0, 0, ILHA.zona_z[1] + 40) + ACIMA, 0.26)
texto('32"  (virada p/ quem chega)', B(0, 0, ILHA.z_frente + 80) + ACIMA, 0.11)
texto("parede c/ janela", (-XP + 0.25, 0, 0.02), 0.15, math.radians(-90))
texto("cortinas", (XP - 0.25, 0, 0.02), 0.15, math.radians(90))
texto("parede do fundo", (0, yp - 0.15, 0.02), 0.13)
for s in (-1, 1):
    rz = math.radians(90 * -s)
    texto(f"zona perto  (fita a {ILHA.perto / 100:.1f} m)", B(s * (ILHA.meia + ILHA.perto / 2), 0, 120) + ACIMA,
          0.12, rz)
    texto(f"zona longe  (registro até {ILHA.longe / 100:.0f} m)",
          B(s * (ILHA.meia + (ILHA.perto + ILHA.longe) / 2), 0, 120) + ACIMA, 0.12, rz)

# ------------------------------------------------------------------ atmosfera e luz
mundo = bpy.data.worlds.new("mundo")
cena.world = mundo
mundo.use_nodes = True
mundo.node_tree.nodes["Background"].inputs["Color"].default_value = (0.006, 0.006, 0.009, 1)
caixa("nevoa", (0, (yp + Y_FRENTE) / 2, 1.6), (2 * XP, comp_lat, 3.2), mat_volume(0.018), bevel=0, lista=SO_3D)
bpy.ops.object.light_add(type="AREA", location=(0, 0.2, 3.3))
teto = bpy.context.object
teto.data.energy, teto.data.size, teto.data.color = 220, 5, (1.0, 0.9, 0.8)
bpy.ops.object.light_add(type="AREA", location=(-XP + 0.4, jy, 1.55))
lua = bpy.context.object
lua.data.energy, lua.data.size, lua.data.color = 60, 1.5, (0.5, 0.65, 1.0)
lua.rotation_euler = (0, math.radians(-90), 0)
bpy.ops.object.light_add(type="AREA", location=(0, 0, 4.0))
planta_luz = bpy.context.object
planta_luz.data.energy, planta_luz.data.size = 900, 9
SO_PLANTA.append(planta_luz)


# ------------------------------------------------------------------ câmeras de render
def cam(nome, pos, alvo, lente=30, orto=None, foco=None):
    bpy.ops.object.camera_add(location=pos)
    c = bpy.context.object
    c.name = nome
    c.rotation_mode = "QUATERNION"
    c.rotation_quaternion = (Vector(alvo) - Vector(pos)).normalized().to_track_quat("-Z", "Y")
    if orto:
        c.data.type, c.data.ortho_scale = "ORTHO", orto
    else:
        c.data.lens = lente
    if foco:
        c.data.dof.use_dof = True
        c.data.dof.focus_distance = foco
        c.data.dof.aperture_fstop = 2.2
    c.data.clip_end = 60
    return c


pe = B(0, 162, ILHA.z_frente + 330)
lat = B(-ILHA.meia - 90, 160, -15)
VISTAS = {
    "1_aerea": (cam("cam_aerea", B(-260, 400, 560), B(0, 60, -20), 22), (1600, 1000)),
    "2_planta": (cam("cam_planta", (0, -0.6, 12), (0, -0.5999, 0), orto=8.6), (1400, 1400)),
    "3_quem_chega": (cam("cam_entrada", pe, B(0, 125, 0), 30, foco=(pe - B(0, 140, ILHA.z_frente + 45)).length),
                     (1600, 1000)),
    "4_lado_esquerdo": (cam("cam_lateral", lat, B(-ILHA.meia, 100, -2), 26, foco=1.0), (1600, 1000)),
    "6_vara_detalhe": (cam("cam_vara", B(-95, 215, 150), B(0, 168, -5), 30), (1600, 1000)),
    "5_vista_lateral": (cam("cam_ampla", B(X_SALA - 25, 175, 250), B(0, 95, -10), 24), (1600, 1000)),
}

try:
    cena.render.engine = "BLENDER_EEVEE"
except TypeError:
    cena.render.engine = "BLENDER_EEVEE_NEXT"
ev = cena.eevee
for attr, val in (("taa_render_samples", 64), ("use_raytracing", True), ("use_shadows", True),
                  ("volumetric_tile_size", "4"), ("volumetric_samples", 96)):
    if hasattr(ev, attr):
        try:
            setattr(ev, attr, val)
        except (TypeError, ValueError):
            pass
try:
    cena.view_settings.view_transform = "AgX"
    cena.view_settings.look = "AgX - Medium High Contrast"
except TypeError:
    pass

SAIDA.mkdir(parents=True, exist_ok=True)
for nome, (c, res) in VISTAS.items():
    planta = nome == "2_planta"
    for o in SO_PLANTA:
        o.hide_render = not planta
    for o in SO_3D:
        o.hide_render = planta
    for o in DONO_ESQ:
        o.hide_render = nome == "4_lado_esquerdo"
    teto.hide_render = planta
    cena.camera = c
    cena.render.resolution_x, cena.render.resolution_y = res
    cena.render.filepath = str(SAIDA / f"render_{nome}.png")
    bpy.ops.render.render(write_still=True)

for o in SO_PLANTA:
    o.hide_render = False
cena.camera = VISTAS["1_aerea"][0]
bpy.ops.wm.save_as_mainfile(filepath=str(SAIDA / "cena_ilha.blend"))
print("OK renders em", SAIDA)
