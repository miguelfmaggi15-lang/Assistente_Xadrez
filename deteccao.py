"""
VISÃO COMPUTACIONAL
===================
Encontra o tabuleiro na tela, reconhece as peças e converte a grade da imagem
em casas de xadrez. O reconhecimento é invariante à cor da casa: a comparação
usa apenas os pixels da peça (máscara de primeiro plano), ignorando o fundo.
"""

import os
from typing import NamedTuple

import chess
import cv2
import numpy as np

TAMANHO_TEMPLATE = 64
KERNEL_ROTULO_RELATIVO = 0.05
EROSAO_NCC = 3
PESO_COR = 0.3
LIMIAR_FUNDO = 35
LIMIAR_VARIACAO_CASA = 12.0
AREA_MINIMA_MASCARA = 0.03
LIMIAR_BRILHO_SELO = 240
AREA_SELO_MINIMA = 0.004
AREA_SELO_MAXIMA = 0.15
OCUPACAO_MINIMA_SELO = 0.45

_MAPEAMENTO_REFERENCIAS = {
    "branco_peao": "P",
    "branco_peão": "P",
    "branco_torre": "R",
    "branco_cavalo": "N",
    "branco_bispo": "B",
    "branco_dama": "Q",
    "branco_rei": "K",
    "preto_peao": "p",
    "preto_peão": "p",
    "preto_torre": "r",
    "preto_cavalo": "n",
    "preto_bispo": "b",
    "preto_dama": "q",
    "preto_rei": "k",
}

SIMBOLOS = {
    "P": "Peão",
    "R": "Torre",
    "N": "Cavalo",
    "B": "Bispo",
    "Q": "Dama",
    "K": "Rei",
    "p": "Peão",
    "r": "Torre",
    "n": "Cavalo",
    "b": "Bispo",
    "q": "Dama",
    "k": "Rei",
}


class Referencia(NamedTuple):
    imagem: np.ndarray
    mascara: np.ndarray
    valores: np.ndarray
    norma: float
    brilho: float


class Grade(NamedTuple):
    tamanho: float
    origem_x: float
    origem_y: float
    pontuacao: float


def carregar_referencias(pasta="referencias"):
    """Carrega as imagens de referência e pré-calcula as máscaras de peça."""
    referencias = {}
    if not os.path.isdir(pasta):
        print("⚠️  Pasta 'referencias/' não encontrada. Usando heurística.")
        return referencias

    for nome, simbolo in _MAPEAMENTO_REFERENCIAS.items():
        caminho = os.path.join(pasta, f"{nome}.png")
        if not os.path.exists(caminho):
            continue
        imagem = cv2.imread(caminho, cv2.IMREAD_COLOR)
        if imagem is None:
            continue
        referencia = _preparar_referencia(imagem)
        if referencia is None:
            print(f"   ⚠️  Referência ignorada (máscara vazia): {nome}.png")
            continue
        referencias[simbolo] = referencia

    print(f"✅ {len(referencias)}/12 referências carregadas")
    return referencias


def _preparar_referencia(imagem):
    mascara_original = _mascara_primeiro_plano(imagem)
    if mascara_original is None:
        return None

    resultado = _normalizar_peca(imagem, mascara_original)
    if resultado is None:
        return None
    imagem_64, mascara_64 = resultado

    if int(mascara_64.sum()) < 50:
        return None

    suave = cv2.GaussianBlur(imagem_64, (5, 5), 1.2)
    valores = suave[mascara_64].astype(np.float32).ravel()
    valores = valores - valores.mean()
    norma = float(np.linalg.norm(valores))
    if norma < 1e-6:
        return None

    cinza = cv2.cvtColor(imagem_64, cv2.COLOR_BGR2GRAY)
    brilho = float(cinza[mascara_64].mean())
    return Referencia(imagem_64, mascara_64, valores, norma, brilho)


def _pixels_ui(imagem):
    """Selos claros de interface (?!, livro) colados na borda superior direita."""
    altura, largura = imagem.shape[:2]
    cinza = cv2.cvtColor(imagem, cv2.COLOR_BGR2GRAY)
    claros = (cinza >= LIMIAR_BRILHO_SELO).astype(np.uint8)
    quantidade, rotulos, estatisticas, centros = cv2.connectedComponentsWithStats(
        claros, connectivity=8
    )
    selecao = np.zeros((altura, largura), np.uint8)
    for indice in range(1, quantidade):
        x, y, largura_componente, altura_componente, area = estatisticas[indice]
        if area < AREA_SELO_MINIMA * altura * largura:
            continue
        if area > AREA_SELO_MAXIMA * altura * largura:
            continue
        ocupacao = area / float(max(1, largura_componente * altura_componente))
        if ocupacao < OCUPACAO_MINIMA_SELO:
            continue
        centro_x, centro_y = centros[indice]
        if centro_x < 0.55 * largura or centro_y > 0.55 * altura:
            continue
        if x + largura_componente < 0.85 * largura and y > 0.12 * altura:
            continue
        selecao[rotulos == indice] = 1
    if selecao.any():
        selecao = cv2.dilate(selecao, np.ones((5, 5), np.uint8))
    return selecao > 0


def _mascara_primeiro_plano(imagem):
    """Máscara dos pixels que não pertencem ao fundo da casa."""
    altura, largura = imagem.shape[:2]
    if altura < 8 or largura < 8:
        return None

    margem = max(2, int(round(min(altura, largura) * 0.04)))
    pontos = (
        (margem, margem),
        (margem, largura // 2),
        (margem, largura - margem - 1),
        (altura // 2, margem),
        (altura // 2, largura - margem - 1),
        (altura - margem - 1, margem),
        (altura - margem - 1, largura // 2),
        (altura - margem - 1, largura - margem - 1),
    )
    amostras = []
    for y, x in pontos:
        trecho = imagem[
            max(0, y - margem):min(altura, y + margem + 1),
            max(0, x - margem):min(largura, x + margem + 1),
        ].reshape(-1, 3).astype(np.float32)
        amostras.append(np.median(trecho, axis=0))

    grupos = []
    for cor in amostras:
        for grupo in grupos:
            if np.linalg.norm(cor - grupo["centro"]) < 40.0:
                grupo["cores"].append(cor)
                grupo["centro"] = np.median(grupo["cores"], axis=0)
                break
        else:
            grupos.append({"centro": cor, "cores": [cor]})
    melhor = max(grupos, key=lambda grupo: len(grupo["cores"]))
    fundo = np.median(melhor["cores"], axis=0)

    pixels = imagem.reshape(-1, 3).astype(np.float32)
    distancias = np.linalg.norm(pixels - fundo, axis=1)
    mascara = (distancias > LIMIAR_FUNDO).astype(np.uint8) * 255
    mascara = mascara.reshape(altura, largura)
    mascara[_pixels_ui(imagem)] = 0

    kernel = np.ones((3, 3), np.uint8)
    mascara = cv2.morphologyEx(mascara, cv2.MORPH_OPEN, kernel)
    mascara = cv2.morphologyEx(mascara, cv2.MORPH_CLOSE, kernel)

    contornos, _ = cv2.findContours(mascara, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contornos:
        return None
    maior = max(contornos, key=cv2.contourArea)
    if cv2.contourArea(maior) < (altura * largura) * AREA_MINIMA_MASCARA:
        return None

    resultado = np.zeros((altura, largura), np.uint8)
    cv2.drawContours(resultado, [maior], -1, 255, -1)
    return resultado


def _tamanho_kernel(mascara):
    lado = min(mascara.shape[:2])
    kernel = max(3, int(round(lado * KERNEL_ROTULO_RELATIVO)))
    return kernel if kernel % 2 == 1 else kernel + 1


def _maior_componente(mascara, kernel):
    """Separa a peça de rótulos finos grudados na base (abre e reconecta)."""
    elementos = np.ones((kernel, kernel), np.uint8)
    erodida = cv2.erode(mascara.astype(np.uint8), elementos)
    quantidade, rotulos, estatisticas, _ = cv2.connectedComponentsWithStats(
        erodida, connectivity=8
    )
    if quantidade <= 1:
        return None
    indice = 1 + int(np.argmax(estatisticas[1:, cv2.CC_STAT_AREA]))
    reconectada = cv2.dilate((rotulos == indice).astype(np.uint8) * 255, elementos)
    restrita = cv2.bitwise_and(mascara.astype(np.uint8), reconectada)

    contornos, _ = cv2.findContours(restrita, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contornos:
        return None
    resultado = np.zeros_like(restrita)
    cv2.drawContours(resultado, [max(contornos, key=cv2.contourArea)], -1, 255, -1)
    return resultado


def _normalizar_peca(imagem, mascara):
    """Recorta a peça pela caixa do contorno e a centraliza em 64x64 pela largura."""
    componente = _maior_componente(mascara, _tamanho_kernel(mascara))
    if componente is None:
        return None

    contornos, _ = cv2.findContours(componente, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contornos:
        return None
    x, y, largura, altura = cv2.boundingRect(max(contornos, key=cv2.contourArea))
    if largura < 8 or altura < 8:
        return None

    escala = TAMANHO_TEMPLATE / float(altura)
    nova_largura = max(1, int(round(largura * escala)))
    recorte = imagem[y:y + altura, x:x + largura]
    recorte_mascara = componente[y:y + altura, x:x + largura]
    ui = _pixels_ui(imagem)[y:y + altura, x:x + largura]
    if ui.any():
        recorte = recorte.copy()
        mediana = np.median(recorte[recorte_mascara > 0], axis=0)
        recorte[ui] = mediana
    imagem_redimensionada = cv2.resize(
        recorte, (nova_largura, TAMANHO_TEMPLATE), interpolation=cv2.INTER_AREA
    )
    mascara_redimensionada = cv2.resize(
        recorte_mascara, (nova_largura, TAMANHO_TEMPLATE), interpolation=cv2.INTER_NEAREST
    ) > 0

    momentos = cv2.moments(mascara_redimensionada.astype(np.uint8), binaryImage=True)
    if momentos["m00"] <= 0:
        return None
    centro_x = momentos["m10"] / momentos["m00"]
    centro_y = momentos["m01"] / momentos["m00"]

    imagem_final = np.zeros((TAMANHO_TEMPLATE, TAMANHO_TEMPLATE, 3), np.uint8)
    mascara_final = np.zeros((TAMANHO_TEMPLATE, TAMANHO_TEMPLATE), bool)
    deslocamento_x = int(round(TAMANHO_TEMPLATE / 2.0 - centro_x))
    deslocamento_y = 0
    origem_x = max(0, -deslocamento_x)
    fim_x = min(nova_largura, TAMANHO_TEMPLATE - deslocamento_x)
    origem_y = max(0, -deslocamento_y)
    fim_y = min(TAMANHO_TEMPLATE, TAMANHO_TEMPLATE - deslocamento_y)
    if fim_x <= origem_x or fim_y <= origem_y:
        return None

    imagem_final[
        origem_y + deslocamento_y:fim_y + deslocamento_y,
        origem_x + deslocamento_x:fim_x + deslocamento_x,
    ] = imagem_redimensionada[origem_y:fim_y, origem_x:fim_x]
    mascara_final[
        origem_y + deslocamento_y:fim_y + deslocamento_y,
        origem_x + deslocamento_x:fim_x + deslocamento_x,
    ] = mascara_redimensionada[origem_y:fim_y, origem_x:fim_x]

    mascara_final = cv2.erode(
        mascara_final.astype(np.uint8), np.ones((EROSAO_NCC, EROSAO_NCC), np.uint8)
    ) > 0
    if int(mascara_final.sum()) < 50:
        return None
    return imagem_final, mascara_final


def _ncc(casa, referencia):
    pixels = casa[referencia.mascara].astype(np.float32).ravel()
    pixels = pixels - pixels.mean()
    norma = float(np.linalg.norm(pixels))
    if norma < 1e-6:
        return 0.0
    return float(np.dot(referencia.valores, pixels) / (referencia.norma * norma))


def _melhor_correspondencia(casa, mascara, referencias):
    melhor_simbolo = None
    melhor_valor = -1.0
    cinza = cv2.cvtColor(casa, cv2.COLOR_BGR2GRAY)
    brilho = float(cinza[mascara].mean())
    suave = cv2.GaussianBlur(casa, (5, 5), 1.2)
    for simbolo, referencia in referencias.items():
        ncc = _ncc(suave, referencia)
        similaridade_cor = 1.0 - min(1.0, abs(brilho - referencia.brilho) / 127.5)
        valor = (1.0 - PESO_COR) * ncc + PESO_COR * similaridade_cor
        if valor > melhor_valor:
            melhor_valor = valor
            melhor_simbolo = simbolo
    return melhor_simbolo, melhor_valor





def _pontuacao_bordas(classe, fundo, tamanho, origem_x, origem_y):
    """
    Fração de fronteiras de casa em que a cor do fundo realmente muda.

    Amostra pixels 2px antes e 2px depois de cada fronteira interna. Numa
    grade alinhada toda fronteira separa casa clara de casa escura. Grades
    deslocadas 2px ou mais caem dentro da mesma casa e não trocam de cor.
    """
    altura, largura = classe.shape
    eixo_y = np.arange(2, altura - 2, 2)
    eixo_x = np.arange(2, largura - 2, 2)
    total = 0
    acertos = 0

    xb = np.round(origem_x + np.arange(1, 8) * tamanho).astype(np.int32)
    xa = xb - 2
    xc = xb + 2
    validos = (xa >= 0) & (xc < largura)
    if np.any(validos):
        xa = xa[validos]
        xc = xc[validos]
        pares = fundo[np.ix_(eixo_y, xa)] & fundo[np.ix_(eixo_y, xc)]
        trocas = (classe[np.ix_(eixo_y, xa)] != classe[np.ix_(eixo_y, xc)]) & pares
        total += int(pares.sum())
        acertos += int(trocas.sum())

    yb = np.round(origem_y + np.arange(1, 8) * tamanho).astype(np.int32)
    ya = yb - 2
    yc = yb + 2
    validos = (ya >= 0) & (yc < altura)
    if np.any(validos):
        ya = ya[validos]
        yc = yc[validos]
        pares = fundo[np.ix_(ya, eixo_x)] & fundo[np.ix_(yc, eixo_x)]
        trocas = (classe[np.ix_(ya, eixo_x)] != classe[np.ix_(yc, eixo_x)]) & pares
        total += int(pares.sum())
        acertos += int(trocas.sum())

    if total < 40:
        return None
    return acertos / float(total)


def calibrar_grade(regiao_tabuleiro):
    """
    Descobre o tamanho real da casa e a origem exata da grade de 8x8.

    O tabuleiro capturado pode ter alguns pixels de borda e a divisão inteira
    (largura // 8) acumula erro de até 7px na última fileira. A busca compara
    a cor de fundo dos cantos de cada casa com o padrão xadrez esperado e
    confirma o alinhamento pelas trocas de cor nas fronteiras das casas.
    """
    altura, largura = regiao_tabuleiro.shape[:2]
    base = min(altura, largura) / 8.0
    if base < 16:
        return Grade(base, 0.0, 0.0, 0.0)

    amostra = regiao_tabuleiro[::4, ::4].reshape(-1, 3).astype(np.float32)
    if len(amostra) < 32:
        return Grade(base, 0.0, 0.0, 0.0)

    criterio = (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 20, 0.5)
    _, rotulos, centros = cv2.kmeans(amostra, 4, None, criterio, 5, cv2.KMEANS_PP_CENTERS)
    contagens = np.bincount(rotulos.ravel(), minlength=4)
    principais = np.argsort(contagens)[-2:]
    principais = principais[np.argsort(centros[principais].sum(axis=1))]
    escura = centros[principais[0]]
    clara = centros[principais[1]]

    pixels = regiao_tabuleiro.astype(np.float32)
    distancia_clara = np.linalg.norm(pixels - clara, axis=2)
    distancia_escura = np.linalg.norm(pixels - escura, axis=2)
    fundo = np.minimum(distancia_clara, distancia_escura) < LIMIAR_FUNDO
    classificado_claro = distancia_clara <= distancia_escura

    passo = 4
    fundo_amostrado = fundo[::passo, ::passo]
    classes = classificado_claro[::passo, ::passo][fundo_amostrado]
    linhas_fundo, colunas_fundo = np.nonzero(fundo_amostrado)
    if len(classes) < 100:
        return Grade(base, 0.0, 0.0, 0.0)
    xs = (colunas_fundo * passo).astype(np.float32)
    ys = (linhas_fundo * passo).astype(np.float32)

    def pontuar(tamanho, origem_x, origem_y):
        colunas = np.floor((xs - origem_x) / tamanho).astype(np.int32)
        linhas = np.floor((ys - origem_y) / tamanho).astype(np.int32)
        dentro = (colunas >= 0) & (colunas < 8) & (linhas >= 0) & (linhas < 8)
        if not np.any(dentro):
            return 0.0
        esperada_clara = ((linhas[dentro] + colunas[dentro]) % 2 == 0)
        pontuacao_pixels = float(np.mean(classes[dentro] == esperada_clara))
        pontuacao_bordas = _pontuacao_bordas(
            classificado_claro, fundo, tamanho, origem_x, origem_y
        )
        if pontuacao_bordas is None:
            return pontuacao_pixels
        return 0.6 * pontuacao_bordas + 0.4 * pontuacao_pixels

    melhor_pontuacao = -1.0
    melhor_tamanho = base
    melhor_x = 0.0
    melhor_y = 0.0
    for tamanho in np.arange(base - 4.0, base + 4.01, 1.0):
        for origem_x in np.arange(-8.0, 10.01, 2.0):
            for origem_y in np.arange(-8.0, 10.01, 2.0):
                pontuacao = pontuar(tamanho, origem_x, origem_y)
                if pontuacao > melhor_pontuacao:
                    melhor_pontuacao = pontuacao
                    melhor_tamanho = float(tamanho)
                    melhor_x = float(origem_x)
                    melhor_y = float(origem_y)

    for tamanho in np.arange(melhor_tamanho - 0.75, melhor_tamanho + 0.751, 0.25):
        for origem_x in np.arange(melhor_x - 1.5, melhor_x + 1.51, 0.5):
            for origem_y in np.arange(melhor_y - 1.5, melhor_y + 1.51, 0.5):
                pontuacao = pontuar(tamanho, origem_x, origem_y)
                if pontuacao > melhor_pontuacao:
                    melhor_pontuacao = pontuacao
                    melhor_tamanho = float(tamanho)
                    melhor_x = float(origem_x)
                    melhor_y = float(origem_y)

    return Grade(melhor_tamanho, melhor_x, melhor_y, melhor_pontuacao)


def detectar_pecas(regiao_tabuleiro, referencias, limiar=0.40, grade_tabuleiro=None):
    """Retorna {(linha, coluna): símbolo} e {(linha, coluna): confiança}."""
    grade = {}
    confiancas = {}

    altura, largura = regiao_tabuleiro.shape[:2]
    if min(altura, largura) / 8.0 < 16:
        return grade, confiancas
    if grade_tabuleiro is None:
        grade_tabuleiro = calibrar_grade(regiao_tabuleiro)

    for linha in range(8):
        for coluna in range(8):
            y0 = max(0, int(round(grade_tabuleiro.origem_y + linha * grade_tabuleiro.tamanho)))
            y1 = min(altura, int(round(grade_tabuleiro.origem_y + (linha + 1) * grade_tabuleiro.tamanho)))
            x0 = max(0, int(round(grade_tabuleiro.origem_x + coluna * grade_tabuleiro.tamanho)))
            x1 = min(largura, int(round(grade_tabuleiro.origem_x + (coluna + 1) * grade_tabuleiro.tamanho)))
            casa = regiao_tabuleiro[y0:y1, x0:x1]
            if casa.size == 0:
                continue
            if float(np.std(casa)) < LIMIAR_VARIACAO_CASA:
                continue

            if referencias:
                mascara = _mascara_primeiro_plano(casa)
                if mascara is None:
                    continue
                resultado = _normalizar_peca(casa, mascara)
                if resultado is None:
                    continue
                casa_normalizada, mascara_normalizada = resultado
                simbolo, confianca = _melhor_correspondencia(
                    casa_normalizada, mascara_normalizada, referencias
                )
                if simbolo is None or confianca < limiar:
                    continue
                grade[(linha, coluna)] = simbolo
                confiancas[(linha, coluna)] = confianca
            else:
                simbolo = _classificar_heuristico(casa)
                if simbolo:
                    grade[(linha, coluna)] = simbolo
                    confiancas[(linha, coluna)] = 0.0

    return grade, confiancas


def _classificar_heuristico(casa):
    """Fallback usado apenas quando não há imagens de referência."""
    altura, largura = casa.shape[:2]
    if float(np.std(casa)) < LIMIAR_VARIACAO_CASA:
        return None

    cinza = cv2.cvtColor(casa, cv2.COLOR_BGR2GRAY)
    borda = max(3, int(min(altura, largura) * 0.12))
    fundo = float(np.median(np.concatenate([
        cinza[:borda, :borda].ravel(),
        cinza[:borda, -borda:].ravel(),
        cinza[-borda:, :borda].ravel(),
        cinza[-borda:, -borda:].ravel(),
    ])))
    centro = cinza[int(altura * 0.25):int(altura * 0.75), int(largura * 0.25):int(largura * 0.75)]
    if centro.size == 0:
        return None
    brilho_centro = float(np.mean(centro))

    if brilho_centro > fundo + 12:
        cor = "branca"
        _, binaria = cv2.threshold(cinza, min(255, int(fundo + 20)), 255, cv2.THRESH_BINARY)
    elif brilho_centro < fundo - 12:
        cor = "preta"
        _, binaria = cv2.threshold(cinza, max(0, int(fundo - 20)), 255, cv2.THRESH_BINARY_INV)
    else:
        return None

    contornos, _ = cv2.findContours(binaria, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contornos:
        return None
    maior = max(contornos, key=cv2.contourArea)
    if cv2.contourArea(maior) / float(altura * largura) < 0.05:
        return None

    _, _, largura_peca, altura_peca = cv2.boundingRect(maior)
    altura_rel = altura_peca / float(altura)
    largura_rel = largura_peca / float(largura)

    if altura_rel < 0.55:
        tipo = "peao"
    elif largura_rel > 0.62 and altura_rel < 0.80:
        tipo = "torre"
    elif altura_rel > 0.80 and largura_rel > 0.50:
        tipo = "rei"
    elif altura_rel > 0.72:
        tipo = "dama"
    elif largura_rel < 0.50:
        tipo = "bispo"
    else:
        tipo = "cavalo"

    return tipo.upper() if cor == "branca" else tipo.lower()


def encontrar_tabuleiro_automatico(img_tela):
    """Localiza o maior retângulo quadrado da tela (o tabuleiro)."""
    cinza = cv2.cvtColor(img_tela, cv2.COLOR_BGR2GRAY)
    bordas = cv2.Canny(cinza, 50, 150)
    contornos, _ = cv2.findContours(bordas, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    candidatos = []
    for contorno in contornos:
        area = cv2.contourArea(contorno)
        if area < 50000:
            continue
        perimetro = cv2.arcLength(contorno, True)
        aproximado = cv2.approxPolyDP(contorno, 0.02 * perimetro, True)
        if len(aproximado) != 4:
            continue
        x, y, largura, altura = cv2.boundingRect(aproximado)
        if altura <= 0 or largura <= 300:
            continue
        proporcao = largura / altura
        if 0.8 < proporcao < 1.2:
            candidatos.append((x, y, largura, altura, area))

    if not candidatos:
        return None
    candidatos.sort(key=lambda candidato: candidato[4], reverse=True)
    return candidatos[0][:4]


def verificar_tabuleiro_valido(img_tela, coords):
    """Confere o padrão xadrez claro/escuro para descartar retângulos falsos."""
    left, top, largura, altura = coords
    regiao = img_tela[top:top + altura, left:left + largura]
    if regiao.size == 0:
        return False

    tamanho_casa = min(largura, altura) // 8
    if tamanho_casa <= 0:
        return False

    corretas = 0
    for linha in range(8):
        for coluna in range(8):
            casa = regiao[
                linha * tamanho_casa:(linha + 1) * tamanho_casa,
                coluna * tamanho_casa:(coluna + 1) * tamanho_casa,
            ]
            if casa.size == 0:
                continue
            brilho = float(np.mean(casa))
            if (linha + coluna) % 2 == 0:
                if brilho > 150:
                    corretas += 1
            elif brilho < 150:
                corretas += 1

    return corretas > 40


def detectar_cor_jogador(grade):
    """
    A cor do jogador é a das peças na parte de baixo da tela.
    Usa as duas últimas linhas da imagem e, se necessário, a mediana das linhas.
    """
    brancas_embaixo = sum(1 for (linha, _), s in grade.items() if linha >= 6 and s.isupper())
    pretas_embaixo = sum(1 for (linha, _), s in grade.items() if linha >= 6 and s.islower())

    if brancas_embaixo > pretas_embaixo:
        return chess.WHITE
    if pretas_embaixo > brancas_embaixo:
        return chess.BLACK

    linhas_brancas = [linha for (linha, _), s in grade.items() if s.isupper()]
    linhas_pretas = [linha for (linha, _), s in grade.items() if s.islower()]
    if linhas_brancas and linhas_pretas:
        return chess.WHITE if np.median(linhas_brancas) > np.median(linhas_pretas) else chess.BLACK
    if linhas_brancas:
        return chess.WHITE
    if linhas_pretas:
        return chess.BLACK
    return None


def casa_de(linha, coluna, cor_jogador):
    """Converte (linha, coluna) da imagem para a casa de xadrez."""
    if cor_jogador == chess.WHITE:
        return chess.square_name(chess.square(coluna, 7 - linha))
    return chess.square_name(chess.square(7 - coluna, linha))


def mapear_pecas(grade, cor_jogador):
    return {casa_de(linha, coluna, cor_jogador): simbolo for (linha, coluna), simbolo in grade.items()}


def montar_board(pecas, turno):
    """Monta o tabuleiro python-chess a partir das peças detectadas."""
    board = chess.Board()
    board.clear()

    for casa, simbolo in pecas.items():
        try:
            board.set_piece_at(chess.parse_square(casa), chess.Piece.from_symbol(simbolo))
        except Exception:
            continue

    board.turn = turno
    _aplicar_direitos_roque(board)
    return board


def _aplicar_direitos_roque(board):
    """Concede roque quando rei e torre estão nas casas iniciais."""
    def ocupada(casa, simbolo):
        peca = board.piece_at(casa)
        return peca is not None and peca.symbol() == simbolo

    direitos = board.castling_rights
    if ocupada(chess.E1, "K") and ocupada(chess.H1, "R"):
        direitos |= chess.BB_H1
    if ocupada(chess.E1, "K") and ocupada(chess.A1, "R"):
        direitos |= chess.BB_A1
    if ocupada(chess.E8, "k") and ocupada(chess.H8, "r"):
        direitos |= chess.BB_H8
    if ocupada(chess.E8, "k") and ocupada(chess.A8, "r"):
        direitos |= chess.BB_A8
    board.castling_rights = direitos


def validar_posicao(pecas):
    """Sanidade mínima para evitar FEN impossível."""
    if len(pecas) < 2:
        return False, "poucas peças"
    if len(pecas) > 32:
        return False, "peças demais"

    contagem = {}
    for simbolo in pecas.values():
        contagem[simbolo] = contagem.get(simbolo, 0) + 1

    reis_brancos = [casa for casa, s in pecas.items() if s == "K"]
    if len(reis_brancos) != 1:
        estado = "ausente" if not reis_brancos else f"duplicado em {', '.join(sorted(reis_brancos))}"
        return False, f"rei branco {estado}"
    reis_pretos = [casa for casa, s in pecas.items() if s == "k"]
    if len(reis_pretos) != 1:
        estado = "ausente" if not reis_pretos else f"duplicado em {', '.join(sorted(reis_pretos))}"
        return False, f"rei preto {estado}"

    brancas = sum(1 for s in pecas.values() if s.isupper())
    pretas = len(pecas) - brancas
    if brancas > 16 or pretas > 16:
        return False, "peças demais de um lado"

    for simbolo, quantidade in contagem.items():
        if simbolo.upper() == "P" and quantidade > 8:
            return False, "peões demais"
        if quantidade > 10:
            return False, "peças repetidas demais"

    for casa, simbolo in pecas.items():
        if simbolo.upper() == "P" and casa[1] in ("1", "8"):
            return False, "peão na última fileira"

    return True, ""


def inferir_lado_movido(pecas_antigas, pecas_novas):
    """Descobre qual cor fez o último lance comparando as duas posições."""
    dados = {}
    for brancas in (True, False):
        antigas = {c: s for c, s in pecas_antigas.items() if s.isupper() == brancas}
        novas = {c: s for c, s in pecas_novas.items() if s.isupper() == brancas}
        ganhos = sum(1 for casa, simbolo in novas.items() if antigas.get(casa) != simbolo)
        perdas = sum(1 for casa, simbolo in antigas.items() if novas.get(casa) != simbolo)
        dados[brancas] = (ganhos, perdas)

    ganhos_brancas, perdas_brancas = dados[True]
    ganhos_pretas, perdas_pretas = dados[False]

    if ganhos_brancas > 0 and ganhos_pretas == 0:
        return chess.WHITE
    if ganhos_pretas > 0 and ganhos_brancas == 0:
        return chess.BLACK
    if ganhos_brancas > ganhos_pretas:
        return chess.WHITE
    if ganhos_pretas > ganhos_brancas:
        return chess.BLACK
    if perdas_brancas > 0 and perdas_pretas == 0:
        return chess.WHITE
    if perdas_pretas > 0 and perdas_brancas == 0:
        return chess.BLACK
    return None


def transicao_plausivel(pecas_antigas, pecas_novas):
    """Um lance legal sempre ocupa uma casa; sumiço isolado é leitura ruim."""
    if pecas_antigas == pecas_novas:
        return True
    for casa, simbolo in pecas_novas.items():
        if pecas_antigas.get(casa) != simbolo:
            return True
    return False


def posicao_inicial():
    board = chess.Board()
    pecas = {}
    for casa in chess.SQUARES:
        peca = board.piece_at(casa)
        if peca is not None:
            pecas[chess.square_name(casa)] = peca.symbol()
    return pecas


POSICAO_INICIAL = posicao_inicial()
