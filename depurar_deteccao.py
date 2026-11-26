"""
DEPURADOR DE DETECÇÃO
=====================
Modos de uso:
    python depurar_deteccao.py --teste
        Monta tabuleiros sintéticos com as peças cruas das referências
        (posição inicial, brancas e pretas embaixo), roda a detecção e
        compara com o esperado. Inclui regressões de ícone branco no canto
        da casa, capturas reais que já travaram a leitura e cenários com
        'fixtures/print_tabuleiro.png' (print original, borda e tabuleiro
        reduzido dentro de um canvas maior). Não precisa de tela.

    python depurar_deteccao.py
        Captura a tela neste instante, procura o tabuleiro, detecta as peças
        e salva 'debug_tabuleiro.png' com as peças marcadas.

    python depurar_deteccao.py --imagem captura.png
        Analisa um arquivo de imagem salvo.
"""

import os
import sys

import chess
import cv2
import numpy as np

import deteccao

TAMANHO_TESTE = 64
PROPORCAO_PECA = 0.72
COR_CLARA = (208, 237, 234)
COR_ESCURA = (87, 149, 118)
LIMIAR = 0.40
PASTA_REFERENCIAS = "referencias"
IMAGEM_REGRESSAO = "fixtures/print_tabuleiro.png"
CAPTURAS_REAIS = {
    "fixtures/debug_leitura_20261019_203008.png": (
        "rnbqkbnr/pppp1ppp/8/4p3/4P3/5N2/PPPP1PPP/RNBQKB1R", ("g1", "f3"), chess.BLACK
    ),
    "fixtures/debug_leitura_20261019_203147.png": (
        "rnbqkbnr/pppp1ppp/8/4p3/4P3/5N2/PPPP1PPP/RNBQKB1R", ("g1", "f3"), chess.BLACK
    ),
    "fixtures/rei_em_g1_com_casa_selecionada.png": (
        "r1b4Q/ppppk3/2n1p1p1/6p1/3P4/2P1qP2/P1P2RPP/R5K1", ("b8", "c6"), chess.WHITE
    ),
}


def carregar_recortes_crus(pasta=PASTA_REFERENCIAS):
    """Recorta cada peça crua (sem erosão) e sua silhueta completa."""
    recortes = {}
    for nome, simbolo in deteccao._MAPEAMENTO_REFERENCIAS.items():
        caminho = os.path.join(pasta, f"{nome}.png")
        if not os.path.exists(caminho):
            continue
        imagem = cv2.imread(caminho, cv2.IMREAD_COLOR)
        if imagem is None:
            continue
        mascara = deteccao._mascara_primeiro_plano(imagem)
        if mascara is None:
            continue
        ys, xs = np.nonzero(mascara)
        y0, y1 = int(ys.min()), int(ys.max()) + 1
        x0, x1 = int(xs.min()), int(xs.max()) + 1
        recortes[simbolo] = (
            imagem[y0:y1, x0:x1],
            mascara[y0:y1, x0:x1] > 0,
        )
    return recortes


def montar_tabuleiro_sintetico(recortes, cor_jogador, board=None, destaque=()):
    lado = 8 * TAMANHO_TESTE
    tabuleiro = np.zeros((lado, lado, 3), np.uint8)

    for linha in range(8):
        for coluna in range(8):
            cor = COR_CLARA if (linha + coluna) % 2 == 0 else COR_ESCURA
            if deteccao.casa_de(linha, coluna, cor_jogador) in destaque:
                cor = (105, 247, 247) if (linha + coluna) % 2 == 0 else (43, 203, 187)
            tabuleiro[
                linha * TAMANHO_TESTE:(linha + 1) * TAMANHO_TESTE,
                coluna * TAMANHO_TESTE:(coluna + 1) * TAMANHO_TESTE,
            ] = cor

    if board is None:
        board = chess.Board()
    for casa in chess.SQUARES:
        peca = board.piece_at(casa)
        if peca is None:
            continue
        recorte = recortes.get(peca.symbol())
        if recorte is None:
            continue

        if cor_jogador == chess.WHITE:
            linha = 7 - chess.square_rank(casa)
            coluna = chess.square_file(casa)
        else:
            linha = chess.square_rank(casa)
            coluna = 7 - chess.square_file(casa)

        imagem, mascara = recorte
        alvo = TAMANHO_TESTE * PROPORCAO_PECA
        fator = alvo / max(imagem.shape[:2])
        novo = (
            max(1, int(round(imagem.shape[1] * fator))),
            max(1, int(round(imagem.shape[0] * fator))),
        )
        redimensionada = cv2.resize(imagem, novo, interpolation=cv2.INTER_AREA)
        mascara_redimensionada = (
            cv2.resize(mascara.astype(np.uint8), novo, interpolation=cv2.INTER_NEAREST) > 0
        )

        y = linha * TAMANHO_TESTE + (TAMANHO_TESTE - novo[1]) // 2
        x = coluna * TAMANHO_TESTE + (TAMANHO_TESTE - novo[0]) // 2
        destino = tabuleiro[y:y + novo[1], x:x + novo[0]]
        destino[mascara_redimensionada] = redimensionada[mascara_redimensionada]

    return tabuleiro


def _avaliar_cenario(nome, cena, referencias, esperado_em_baixo=chess.WHITE):
    grade_calibrada = deteccao.calibrar_grade(cena)
    grade, confiancas = deteccao.detectar_pecas(
        cena, referencias, LIMIAR, grade_calibrada
    )
    pecas = deteccao.mapear_pecas(grade, esperado_em_baixo)
    valido, motivo = deteccao.validar_posicao(pecas)
    divergentes = {
        casa: (simbolo, pecas.get(casa))
        for casa, simbolo in deteccao.POSICAO_INICIAL.items()
        if pecas.get(casa) != simbolo
    }
    ok = len(grade) == 32 and valido and not divergentes

    print(
        f"{'✅' if ok else '❌'} {nome}: "
        f"size={grade_calibrada.tamanho:.2f} "
        f"ox={grade_calibrada.origem_x:.1f} "
        f"oy={grade_calibrada.origem_y:.1f} "
        f"score={grade_calibrada.pontuacao:.4f} | peças={len(grade)}"
    )
    if divergentes:
        print(f"   divergentes (esperado, detectado): {divergentes}")
    if not valido:
        print(f"   validação falhou: {motivo}")
    return ok, grade, confiancas


def _teste_icone_no_canto(referencias, recortes):
    """Regressão: ícone branco no canto da casa não pode apagar a peça."""
    tabuleiro = montar_tabuleiro_sintetico(recortes, chess.WHITE)
    linha, coluna = 7, 1
    x = coluna * TAMANHO_TESTE + TAMANHO_TESTE - 11
    y = linha * TAMANHO_TESTE + 1
    cv2.rectangle(tabuleiro, (x, y), (x + 10, y + 10), (250, 250, 250), -1)
    cv2.rectangle(tabuleiro, (x, y), (x + 10, y + 10), (20, 20, 20), 1)
    ok, _, _ = _avaliar_cenario(
        "ícone branco no canto de b1", tabuleiro, referencias
    )
    return ok


def executar_regressoes_reais(referencias):
    """Capturas reais da sessão que antes travavam a leitura."""
    encontrados = 0
    tudo_ok = True
    for nome, (fen_esperado, lance_esperado, vez_esperada) in CAPTURAS_REAIS.items():
        if not os.path.exists(nome):
            continue
        encontrados += 1
        imagem = cv2.imread(nome, cv2.IMREAD_COLOR)
        if imagem is None:
            print(f"❌ Não foi possível ler '{nome}'")
            tudo_ok = False
            continue
        grade_calibrada = deteccao.calibrar_grade(imagem)
        grade, _ = deteccao.detectar_pecas(
            imagem, referencias, LIMIAR, grade_calibrada
        )
        pecas = deteccao.mapear_pecas(grade, chess.WHITE)
        valido, motivo = deteccao.validar_posicao(pecas)
        fen = deteccao.montar_board(pecas, chess.WHITE).fen().split(" ")[0]
        orientacao = deteccao.detectar_orientacao(imagem, grade_calibrada)
        destaques = deteccao.detectar_lances_destacados(
            imagem, grade_calibrada, grade, chess.WHITE
        )
        board = deteccao.sincronizar_pelo_destaque(
            deteccao.tabuleiros_possiveis(pecas), destaques
        )
        ok = (
            valido and fen == fen_esperado and orientacao == chess.WHITE
            and lance_esperado in destaques and board is not None and board.turn == vez_esperada
        )
        tudo_ok = tudo_ok and ok
        print(
            f"{'✅' if ok else '❌'} {nome}: peças={len(grade)} FEN={fen}"
            f" | orientação={orientacao} | destaques={destaques}"
            + ("" if valido else f" | inválido: {motivo}")
        )
    if encontrados == 0:
        print("⚠️  Capturas reais de regressão ausentes; ignoradas.")
        return None
    return tudo_ok


def executar_regressoes(referencias):
    """Cenários reais derivados de 'fixtures/print_tabuleiro.png'."""
    if not os.path.exists(IMAGEM_REGRESSAO):
        print(f"⚠️  '{IMAGEM_REGRESSAO}' não encontrado; regressões ignoradas.")
        return None

    imagem = cv2.imread(IMAGEM_REGRESSAO, cv2.IMREAD_COLOR)
    if imagem is None:
        print(f"❌ Não foi possível ler '{IMAGEM_REGRESSAO}'")
        return False

    cenarios = [("print original", imagem)]

    borda = np.full(
        (imagem.shape[0] + 10, imagem.shape[1] + 10, 3), 180, np.uint8
    )
    borda[5:5 + imagem.shape[0], 5:5 + imagem.shape[1]] = imagem
    cenarios.append(("print com borda 5px", borda))

    reduzido = cv2.resize(imagem, (800, 800), interpolation=cv2.INTER_AREA)
    canvas = np.full((808, 808, 3), 180, np.uint8)
    canvas[4:804, 4:804] = reduzido
    cenarios.append(("tabuleiro 800px em canvas 808", canvas))

    tudo_ok = True
    for nome, cena in cenarios:
        ok, _, _ = _avaliar_cenario(nome, cena, referencias)
        tudo_ok = tudo_ok and ok
    return tudo_ok


def _desenhar_selo(imagem, x, y, largura, altura):
    """Desenha um selo claro de anotação ('?!') com borda escura."""
    cv2.rectangle(imagem, (x, y), (x + largura, y + altura), (255, 255, 255), -1)
    cv2.rectangle(imagem, (x, y), (x + largura, y + altura), (25, 25, 25), 1)
    escala = max(0.3, min(largura, altura) / 34.0)
    cv2.putText(
        imagem,
        "?!",
        (x + 2, y + int(altura * 0.72)),
        cv2.FONT_HERSHEY_SIMPLEX,
        escala,
        (10, 10, 10),
        1,
        cv2.LINE_AA,
    )


def _teste_selos_print(referencias):
    """Selos '?!' sobre a dama preta d8 e a torre branca h1 do print real."""
    if not os.path.exists(IMAGEM_REGRESSAO):
        print(f"⚠️  '{IMAGEM_REGRESSAO}' ausente; teste de selos ignorado.")
        return None
    imagem = cv2.imread(IMAGEM_REGRESSAO, cv2.IMREAD_COLOR)
    grade = deteccao.calibrar_grade(imagem)
    for linha, coluna in ((0, 3), (7, 7)):
        x0 = int(round(grade.origem_x + coluna * grade.tamanho))
        y0 = int(round(grade.origem_y + linha * grade.tamanho))
        _desenhar_selo(
            imagem,
            x0 + int(round(grade.tamanho * 0.72)),
            y0 + max(2, int(round(grade.tamanho * 0.03))),
            max(10, int(round(grade.tamanho * 0.2))),
            max(10, int(round(grade.tamanho * 0.24))),
        )
    ok, _, _ = _avaliar_cenario(
        "selos '?!' sobre dama d8 e torre h1", imagem, referencias
    )
    return ok


def _teste_selo_celula(referencias, recortes):
    """Selo encostado na peça não pode derrubar a classificação da célula."""
    casos = (
        ("dama preta em casa destacada", "q", (80, 170, 180)),
        ("cavalo branco em casa clara", "N", (234, 237, 208)),
    )
    tudo_ok = True
    for nome, simbolo, fundo in casos:
        imagem, mascara = recortes[simbolo]
        celula = np.zeros((TAMANHO_TESTE, TAMANHO_TESTE, 3), np.uint8)
        celula[:] = fundo
        alvo = int(round(TAMANHO_TESTE * PROPORCAO_PECA))
        fator = alvo / mascara.shape[0]
        novo = (
            max(1, int(round(mascara.shape[1] * fator))),
            alvo,
        )
        redimensionada = cv2.resize(imagem, novo, interpolation=cv2.INTER_AREA)
        mascara_redimensionada = (
            cv2.resize(mascara.astype(np.uint8), novo, interpolation=cv2.INTER_NEAREST) > 0
        )
        x = (TAMANHO_TESTE - novo[0]) // 2
        y = TAMANHO_TESTE - novo[1] - 4
        destino = celula[y:y + novo[1], x:x + novo[0]]
        destino[mascara_redimensionada] = redimensionada[mascara_redimensionada]
        _desenhar_selo(celula, TAMANHO_TESTE - 15, 6, 12, 14)

        mascara_celula = deteccao._mascara_primeiro_plano(celula)
        resultado = (
            None
            if mascara_celula is None
            else deteccao._normalizar_peca(celula, mascara_celula)
        )
        if resultado is None:
            print(f"❌ {nome}: célula não segmentada")
            tudo_ok = False
            continue
        detectado, confianca = deteccao._melhor_correspondencia(
            resultado[0], resultado[1], referencias
        )
        ok = detectado == simbolo and confianca >= LIMIAR
        tudo_ok = tudo_ok and ok
        print(
            f"{'✅' if ok else '❌'} {nome}: {detectado} {confianca:.3f} "
            f"(esperado {simbolo})"
        )
    return tudo_ok


def _teste_transicao_plausivel():
    """Sumiço de peça sem destino é leitura ruim; lance legal é plausível."""
    antigas = {
        "a8": "r", "b8": "n", "c8": "b", "d8": "q", "e8": "k",
        "f8": "b", "g8": "n", "h8": "r",
        "a7": "p", "b7": "p", "c7": "p", "d7": "p", "e6": "p",
        "f7": "p", "g7": "p", "h7": "p",
        "d4": "P", "e4": "P", "a2": "P", "b2": "P", "c2": "P",
        "f2": "P", "g2": "P", "h2": "P",
        "a1": "R", "b1": "N", "c1": "B", "d1": "Q", "e1": "K",
        "f1": "B", "g1": "N", "h1": "R",
    }
    sem_dama = {casa: s for casa, s in antigas.items() if casa != "d8"}
    qh4 = dict(sem_dama)
    qh4["h4"] = "q"

    casos = (
        ("dama desapareceu (leitura ruim)", antigas, sem_dama, False),
        ("lance Qh4 (dama em h4)", antigas, qh4, True),
        ("posição igual", antigas, dict(antigas), True),
    )
    tudo_ok = True
    for nome, velhas, novas, esperado in casos:
        obtido = deteccao.transicao_plausivel(velhas, novas)
        ok = obtido == esperado
        tudo_ok = tudo_ok and ok
        print(f"{'✅' if ok else '❌'} {nome}: {obtido} (esperado {esperado})")
    return tudo_ok


def _teste_abrir_em_final(referencias, recortes):
    board = chess.Board("8/3p4/8/2P5/8/3k4/8/3K4 b - - 0 1")
    board.push_uci("d7d5")
    depois = board.copy()
    depois.push_uci("c5d6")
    tudo_ok = True
    for cor in (chess.WHITE, chess.BLACK):
        imagem = montar_tabuleiro_sintetico(recortes, cor, board, ("d7", "d5"))
        calibracao = deteccao.calibrar_grade(imagem)
        grade, _ = deteccao.detectar_pecas(imagem, referencias, LIMIAR, calibracao)
        destaques = deteccao.detectar_lances_destacados(imagem, calibracao, grade, cor)
        pecas = deteccao.mapear_pecas(grade, cor)
        candidatos = deteccao.tabuleiros_possiveis(pecas)
        atual = deteccao.sincronizar_pelo_destaque(candidatos, destaques)
        avancado = deteccao.avancar_posicao([atual], depois) if atual else None
        ok = (
            len(grade) == 4 and len(candidatos) == 2 and destaques == [("d7", "d5")]
            and atual is not None and atual.board_fen() == board.board_fen()
            and atual.turn == chess.WHITE and atual.has_legal_en_passant()
            and avancado is not None and avancado.turn == chess.BLACK
            and avancado.board_fen() == depois.board_fen()
        )
        print(f"{'✅' if ok else '❌'} Final com 4 peças e en passant | brancas embaixo={cor}")
        tudo_ok = tudo_ok and ok
    return tudo_ok


def executar_teste(referencias):
    if not referencias:
        print("❌ Nenhuma referência carregada. Coloque as imagens em 'referencias/'.")
        return False

    recortes = carregar_recortes_crus()
    if not recortes:
        print("❌ Não foi possível recortar as peças cruas das referências.")
        return False

    tudo_ok = True
    for cor_jogador in (chess.WHITE, chess.BLACK):
        nome = "Brancas embaixo" if cor_jogador == chess.WHITE else "Pretas embaixo"
        print(f"--- {nome} ---")

        tabuleiro = montar_tabuleiro_sintetico(recortes, cor_jogador)
        cv2.imwrite(f"debug_sintetico_{nome.split()[0].lower()}.png", tabuleiro)

        grade_calibrada = deteccao.calibrar_grade(tabuleiro)
        grade, confiancas = deteccao.detectar_pecas(
            tabuleiro, referencias, LIMIAR, grade_calibrada
        )
        cor_detectada = deteccao.detectar_cor_jogador(grade)
        nomes_cores = {chess.WHITE: "Brancas", chess.BLACK: "Pretas", None: "indefinida"}
        print(f"Cor detectada: {nomes_cores[cor_detectada]} | peças: {len(grade)}")
        _imprimir_grade(grade, confiancas)

        pecas = deteccao.mapear_pecas(grade, cor_jogador)
        esperado = deteccao.POSICAO_INICIAL

        divergentes = {
            casa: (simbolo, pecas.get(casa))
            for casa, simbolo in esperado.items()
            if pecas.get(casa) != simbolo
        }
        extras = {
            casa: simbolo
            for casa, simbolo in pecas.items()
            if esperado.get(casa) != simbolo
        }

        if divergentes or extras:
            tudo_ok = False
            print(f"❌ Divergentes (esperado, detectado): {divergentes}")
            print(f"❌ Extras: {extras}")
        else:
            print("✅ 32 peças corretas na posição inicial")

        if cor_detectada != cor_jogador:
            tudo_ok = False
            print(f"❌ Cor do jogador errada: esperado {nomes_cores[cor_jogador]}")

        board = deteccao.montar_board(pecas, chess.WHITE)
        print(f"FEN: {board.fen()}\n")

    print("--- Regressão: ícone branco no canto da casa ---")
    if not _teste_icone_no_canto(referencias, recortes):
        tudo_ok = False

    print("--- Regressão: selo '?!' encostado na peça ---")
    if not _teste_selo_celula(referencias, recortes):
        tudo_ok = False
    resultado_selos = _teste_selos_print(referencias)
    if resultado_selos is not None and not resultado_selos:
        tudo_ok = False

    print("--- Regressão: transições de posição ---")
    if not _teste_transicao_plausivel():
        tudo_ok = False

    print("--- Regressão: abrir com a partida em andamento ---")
    if not _teste_abrir_em_final(referencias, recortes):
        tudo_ok = False

    print("--- Regressões com capturas reais ---")
    resultado_reais = executar_regressoes_reais(referencias)
    if resultado_reais is not None:
        tudo_ok = tudo_ok and resultado_reais

    print("--- Regressões com print real ---")
    resultado = executar_regressoes(referencias)
    if resultado is not None:
        tudo_ok = tudo_ok and resultado
    return tudo_ok


def analisar_imagem(imagem, referencias):
    coords = deteccao.encontrar_tabuleiro_automatico(imagem)
    if coords is None or not deteccao.verificar_tabuleiro_valido(imagem, coords):
        print("❌ Tabuleiro não encontrado na imagem.")
        cv2.imwrite("debug_tela.png", imagem)
        print("   Captura salva em debug_tela.png")
        return

    left, top, largura, altura = coords
    print(f"✅ Tabuleiro: ({left}, {top}) {largura}x{altura}")
    regiao = imagem[top:top + altura, left:left + largura]

    grade_tabuleiro = deteccao.calibrar_grade(regiao)
    print(
        f"   Calibração: size={grade_tabuleiro.tamanho:.2f} "
        f"ox={grade_tabuleiro.origem_x:.1f} oy={grade_tabuleiro.origem_y:.1f} "
        f"score={grade_tabuleiro.pontuacao:.4f}"
    )

    grade, confiancas = deteccao.detectar_pecas(
        regiao, referencias, LIMIAR, grade_tabuleiro
    )
    cor = deteccao.detectar_cor_jogador(grade)
    nomes_cores = {chess.WHITE: "Brancas", chess.BLACK: "Pretas", None: "indefinida"}
    print(f"Cor do jogador: {nomes_cores[cor]} | peças detectadas: {len(grade)}")
    _imprimir_grade(grade, confiancas)

    cv2.imwrite(
        "debug_tabuleiro.png",
        _anotar(regiao, grade, confiancas, grade_tabuleiro),
    )
    print("Imagem anotada salva em debug_tabuleiro.png")

    if cor is None:
        return

    pecas = deteccao.mapear_pecas(grade, cor)
    valido, motivo = deteccao.validar_posicao(pecas)
    print("Validação:", "OK" if valido else f"falhou ({motivo})")
    board = deteccao.montar_board(pecas, cor)
    print(f"FEN: {board.fen()}")


def _imprimir_grade(grade, confiancas):
    for linha in range(8):
        partes = [grade.get((linha, coluna), ".") for coluna in range(8)]
        print("   " + " ".join(partes))
    baixas = sorted(
        (chave, round(valor, 2)) for chave, valor in confiancas.items() if valor < 0.8
    )
    if baixas:
        print(f"   Confianças baixas (<0.8): {baixas}")


def _anotar(regiao, grade, confiancas, grade_tabuleiro=None):
    imagem = regiao.copy()
    if grade_tabuleiro is None:
        grade_tabuleiro = deteccao.calibrar_grade(regiao)
    tamanho = grade_tabuleiro.tamanho
    for (linha, coluna), simbolo in grade.items():
        if (linha, coluna) not in confiancas:
            continue
        confianca = confiancas[(linha, coluna)]
        cor = (0, 255, 0) if confianca >= 0.8 else (0, 165, 255)
        x = int(
            grade_tabuleiro.origem_x
            + coluna * tamanho
            + tamanho / 2
            - 8
        )
        y = int(
            grade_tabuleiro.origem_y
            + linha * tamanho
            + tamanho / 2
            + 6
        )
        cv2.putText(imagem, simbolo, (x, y), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 0), 3)
        cv2.putText(imagem, simbolo, (x, y), cv2.FONT_HERSHEY_SIMPLEX, 0.7, cor, 1)
    return imagem


def _abrir_mss():
    import mss

    return mss.MSS() if hasattr(mss, "MSS") else mss.mss()


def main():
    argumentos = sys.argv[1:]
    print("🔧 Carregando referências...")
    referencias = deteccao.carregar_referencias()
    print()

    if "--teste" in argumentos:
        sucesso = executar_teste(referencias)
        sys.exit(0 if sucesso else 1)

    if "--imagem" in argumentos:
        indice = argumentos.index("--imagem")
        if indice + 1 >= len(argumentos):
            print("❌ Use: python depurar_deteccao.py --imagem caminho.png")
            sys.exit(1)
        caminho = argumentos[indice + 1]
        imagem = cv2.imread(caminho)
        if imagem is None:
            print(f"❌ Não foi possível ler '{caminho}'")
            sys.exit(1)
        analisar_imagem(imagem, referencias)
        return

    with _abrir_mss() as sct:
        captura = np.array(sct.grab(sct.monitors[0]))
    imagem = cv2.cvtColor(captura, cv2.COLOR_BGRA2BGR)
    analisar_imagem(imagem, referencias)


if __name__ == "__main__":
    main()
