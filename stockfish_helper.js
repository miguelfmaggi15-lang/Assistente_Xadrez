"use strict";

const initEngine = require("stockfish");
const readline = require("readline");

const engineDepth = parseInt(process.env.SF_DEPTH || "", 10) || 15;
const engineMovetime = parseInt(process.env.SF_MOVETIME || "", 10) || 0;

const engine = initEngine();

let fila = [];
let buscando = false;
let pronto = false;

function responder(id, jogada) {
  const prefixo = id ? `${id} ` : "";
  process.stdout.write(`${prefixo}${jogada}\n`);
}

function iniciarBusca() {
  if (!pronto || buscando || fila.length === 0) return;

  const item = fila[0];
  buscando = true;

  try {
    engine.processCommand(`position fen ${item.fen}`);
    if (engineMovetime > 0) {
      engine.processCommand(`go movetime ${engineMovetime}`);
    } else {
      engine.processCommand(`go depth ${engineDepth}`);
    }
  } catch (erro) {
    fila.shift();
    buscando = false;
    responder(item.id, "none");
    process.stderr.write(`erro ao calcular: ${erro.message}\n`);
    iniciarBusca();
  }
}

engine.listener = (linha) => {
  const texto = String(linha).trim();

  if (!pronto) {
    if (texto === "readyok") {
      pronto = true;
      iniciarBusca();
    }
    return;
  }

  if (!texto.startsWith("bestmove")) return;

  const partes = texto.split(/\s+/);
  const jogada = partes[1] || "none";
  if (fila.length > 0) {
    const item = fila.shift();
    responder(item.id, jogada);
  }
  buscando = false;
  iniciarBusca();
};

engine.processCommand("uci");
engine.processCommand("isready");

setTimeout(() => {
  if (!pronto) {
    pronto = true;
    iniciarBusca();
  }
}, 2000);

const leitor = readline.createInterface({ input: process.stdin, terminal: false });

const keepAlive = setInterval(() => {}, 3600000);

leitor.on("close", () => {
  const verificador = setInterval(() => {
    if (!buscando && fila.length === 0) {
      clearInterval(verificador);
      clearInterval(keepAlive);
      process.exit(0);
    }
  }, 100);
});

leitor.on("line", (linha) => {
  const texto = linha.trim();
  if (!texto) return;

  const separador = texto.indexOf("|");
  const id = separador >= 0 ? texto.slice(0, separador).trim() : "";
  const fen = separador >= 0 ? texto.slice(separador + 1).trim() : texto;
  if (!fen) return;

  fila.push({ id, fen });
  iniciarBusca();
});
