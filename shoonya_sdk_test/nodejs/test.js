const fs = require("fs");
const path = require("path");
const crypto = require("crypto");
const fernet = require("fernet");
const { Client } = require("pg");

// The upstream SDK logs raw request payloads (including jKey) via console.
// Silence SDK-owned console output to keep logs safe and readable.
console.log = () => {};
console.error = () => {};

const repoRoot = path.resolve(__dirname, "../..");
require("dotenv").config({ path: path.join(repoRoot, ".env") });

const sdkConfig = require("shoonya-api-js/lib/config");
sdkConfig.API.endpoint = "https://api.shoonya.com/NorenWClientAPI/";

const ShoonyaApi = require("shoonya-api-js/lib/RestApi");

const DEFAULT_SYMBOLS = ["BSXOPT02JUL2678400CE", "BSXOPT02JUL2677900CE"];

function parseArgs() {
  const args = process.argv.slice(2);
  const out = {
    userId: 1,
    symbols: DEFAULT_SYMBOLS.join(","),
    durationSec: 300,
    perCallSleepSec: 0.4,
    logDir: "shoonya_sdk_test/python/logs",
  };

  for (let i = 0; i < args.length; i += 1) {
    const arg = args[i];
    const next = args[i + 1];
    if (arg === "--user-id" && next) {
      out.userId = Number(next);
      i += 1;
    } else if (arg === "--symbols" && next) {
      out.symbols = next;
      i += 1;
    } else if (arg === "--duration-sec" && next) {
      out.durationSec = Number(next);
      i += 1;
    } else if (arg === "--per-call-sleep-sec" && next) {
      out.perCallSleepSec = Number(next);
      i += 1;
    } else if (arg === "--log-dir" && next) {
      out.logDir = next;
      i += 1;
    }
  }

  return out;
}

function normalizeDbUrl(rawDbUrl) {
  if (!rawDbUrl) {
    throw new Error("DATABASE_URL is missing");
  }
  return rawDbUrl.replace("postgresql+asyncpg://", "postgresql://").replace("postgresql+psycopg://", "postgresql://");
}

function deriveFernetKey(encryptionPepper) {
  const key = crypto.pbkdf2Sync(
    Buffer.from(encryptionPepper, "utf8"),
    Buffer.from("openbull_static_salt", "utf8"),
    100000,
    32,
    "sha256",
  );
  return key.toString("base64url");
}

function decryptValue(ciphertext, encryptionPepper) {
  const secret = new fernet.Secret(deriveFernetKey(encryptionPepper));
  const token = new fernet.Token({
    secret,
    token: ciphertext,
    ttl: 0,
  });
  return token.decode();
}

function parseAuthToken(authToken) {
  const parts = authToken ? authToken.split(":") : [];
  if (parts.length >= 3) return [parts[0], parts[1], parts[2]];
  if (parts.length === 2) return [parts[0], parts[1], parts[0]];
  if (parts.length === 1) return ["", parts[0], ""];
  return ["", "", ""];
}

function nowIstIsoLike() {
  return new Date().toLocaleString("sv-SE", { timeZone: "Asia/Kolkata" }).replace(" ", "T") + "+0530";
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function createLogger(logFilePath) {
  const stream = fs.createWriteStream(logFilePath, { flags: "a" });

  function write(level, message) {
    const line = `${new Date().toISOString()} | ${level} | ${message}`;
    process.stdout.write(line + "\n");
    stream.write(line + "\n");
  }

  return {
    info: (msg) => write("INFO", msg),
    warn: (msg) => write("WARN", msg),
    error: (msg) => write("ERROR", msg),
    close: () => stream.end(),
  };
}

async function loadLiveAuth(client, userId, encryptionPepper) {
  const authRes = await client.query(
    `
      SELECT access_token
      FROM broker_auth
      WHERE user_id = $1
        AND broker_name = 'shoonya'
        AND is_revoked = false
      LIMIT 1
    `,
    [userId],
  );

  if (authRes.rows.length === 0 || !authRes.rows[0].access_token) {
    throw new Error("No active Shoonya session found in DB");
  }

  return decryptValue(authRes.rows[0].access_token, encryptionPepper);
}

async function resolveInstruments(client, symbols) {
  const res = await client.query(
    `
      SELECT symbol, exchange, token
      FROM symtoken
      WHERE symbol = ANY($1::text[])
      ORDER BY symbol ASC
    `,
    [symbols],
  );

  const found = new Map();
  for (const row of res.rows) {
    if (row.token) {
      found.set(row.symbol, {
        symbol: row.symbol,
        exchange: row.exchange,
        token: String(row.token),
      });
    }
  }

  const instruments = [];
  for (const symbol of symbols) {
    if (found.has(symbol)) {
      instruments.push(found.get(symbol));
    }
  }

  instruments.push({ symbol: "SENSEX", exchange: "BSE", token: "1" });
  return instruments;
}

function validateResponse(reqSymbol, reqExchange, reqToken, response) {
  const info = {
    req_symbol: reqSymbol,
    req_exchange: reqExchange,
    req_token: String(reqToken),
    ok: false,
    token_match: false,
    classification: "unknown",
    ltp: null,
    resp_token: null,
    resp_stat: null,
    error: null,
  };

  if (!response || typeof response !== "object") {
    info.classification = "invalid_payload";
    info.error = "SDK returned non-object payload";
    return info;
  }

  info.resp_stat = response.stat ?? null;
  info.resp_token = response.token != null ? String(response.token) : null;
  info.ltp = response.lp ?? null;

  if (response.stat !== "Ok") {
    info.classification = "api_not_ok";
    info.error = response.emsg || "Unknown API error";
    return info;
  }

  info.ok = true;
  info.token_match = String(response.token) === String(reqToken);

  if (info.token_match) {
    info.classification = "ok";
  } else if (reqExchange === "BFO" && String(response.token) === "1") {
    info.classification = "bfo_option_returned_sensex_token";
  } else {
    info.classification = "token_mismatch";
  }

  return info;
}

async function runValidationLoop({ api, instruments, durationSec, perCallSleepSec, jsonlFilePath, logger }) {
  const started = Date.now();
  const counts = {
    total_calls: 0,
    ok: 0,
    api_not_ok: 0,
    token_match: 0,
    token_mismatch: 0,
    bfo_option_returned_sensex_token: 0,
    other_errors: 0,
  };

  const jsonlStream = fs.createWriteStream(jsonlFilePath, { flags: "w" });

  try {
    while ((Date.now() - started) / 1000 < durationSec) {
      for (const ins of instruments) {
        if ((Date.now() - started) / 1000 >= durationSec) break;

        const symbol = ins.symbol;
        const exchange = ins.exchange;
        const token = ins.token;
        counts.total_calls += 1;

        let result;
        try {
          // SDK method returns a Promise with JSON payload.
          const raw = await api.get_quotes(exchange, token);
          result = validateResponse(symbol, exchange, token, raw);
        } catch (error) {
          result = {
            req_symbol: symbol,
            req_exchange: exchange,
            req_token: token,
            ok: false,
            token_match: false,
            classification: "exception",
            ltp: null,
            resp_token: null,
            resp_stat: null,
            error: String(error && error.message ? error.message : error),
          };
        }

        if (result.ok) counts.ok += 1;
        if (result.classification === "api_not_ok") counts.api_not_ok += 1;
        if (result.token_match) {
          counts.token_match += 1;
        } else if (result.classification === "bfo_option_returned_sensex_token") {
          counts.bfo_option_returned_sensex_token += 1;
          counts.token_mismatch += 1;
        } else if (result.classification === "token_mismatch") {
          counts.token_mismatch += 1;
        } else if (result.classification === "exception") {
          counts.other_errors += 1;
        }

        const row = {
          ts: nowIstIsoLike(),
          ...result,
        };
        jsonlStream.write(JSON.stringify(row) + "\n");

        if (result.classification === "ok") {
          logger.info(`OK ${symbol}/${exchange} token=${token} ltp=${result.ltp}`);
        } else {
          logger.warn(
            `ISSUE ${result.classification} ${symbol}/${exchange} req_token=${token} resp_token=${result.resp_token} stat=${result.resp_stat} err=${result.error}`,
          );
        }

        await sleep(Math.max(0, perCallSleepSec * 1000));
      }
    }
  } finally {
    jsonlStream.end();
  }

  return counts;
}

async function main() {
  const args = parseArgs();

  const encryptionPepper = process.env.ENCRYPTION_PEPPER || process.env.encryption_pepper;
  if (!encryptionPepper) {
    throw new Error("ENCRYPTION_PEPPER is missing in environment");
  }

  const dbUrl = normalizeDbUrl(process.env.DATABASE_URL || process.env.database_url || "postgresql+asyncpg://postgres:123456@localhost:5432/openbull");

  const symbols = args.symbols
    .split(",")
    .map((s) => s.trim())
    .filter(Boolean);

  const resolvedSymbols = symbols.length > 0 ? symbols : DEFAULT_SYMBOLS;

  const outputDir = path.resolve(repoRoot, args.logDir);
  fs.mkdirSync(outputDir, { recursive: true });

  const stamp = new Date().toISOString().replace(/[-:]/g, "").replace(/\.\d+Z$/, "").replace("T", "_");
  const logFilePath = path.join(outputDir, `getquotes_validation_${stamp}.log`);
  const jsonlFilePath = path.join(outputDir, `getquotes_validation_${stamp}.jsonl`);

  const logger = createLogger(logFilePath);
  const dbClient = new Client({ connectionString: dbUrl });

  try {
    await dbClient.connect();

    const authToken = await loadLiveAuth(dbClient, args.userId, encryptionPepper);
    const [uid, jkey] = parseAuthToken(authToken);
    if (!uid || !jkey) {
      throw new Error("Invalid Shoonya auth token format in DB");
    }

    const instruments = await resolveInstruments(dbClient, resolvedSymbols);
    const requested = new Set(resolvedSymbols);
    const found = new Set(instruments.map((x) => x.symbol));
    const missing = [...requested].filter((s) => !found.has(s)).sort();

    logger.info("Starting Shoonya GetQuotes validation (Node.js SDK)");
    logger.info(`Duration=${args.durationSec}s per_call_sleep=${args.perCallSleepSec.toFixed(3)}s`);
    logger.info(`Resolved instruments=${JSON.stringify(instruments)}`);
    if (missing.length) {
      logger.warn(`Symbols not found in symtoken table: ${JSON.stringify(missing)}`);
    }

    const api = new ShoonyaApi({});
    api.setSessionDetails({
      susertoken: jkey,
      actid: uid,
    });

    const counts = await runValidationLoop({
      api,
      instruments,
      durationSec: args.durationSec,
      perCallSleepSec: args.perCallSleepSec,
      jsonlFilePath,
      logger,
    });

    logger.info("Validation finished");
    logger.info(`Summary: ${JSON.stringify(counts)}`);
    logger.info(`Log file: ${logFilePath}`);
    logger.info(`JSONL file: ${jsonlFilePath}`);

    process.stdout.write("\n=== FINAL SUMMARY ===\n");
    process.stdout.write(`${JSON.stringify(counts, null, 2)}\n`);
    process.stdout.write(`log_file=${logFilePath}\n`);
    process.stdout.write(`jsonl_file=${jsonlFilePath}\n`);
  } finally {
    await dbClient.end().catch(() => undefined);
    logger.close();
  }
}

main().catch((err) => {
  process.stderr.write(`ERROR: ${err && err.stack ? err.stack : err}\n`);
  process.exit(1);
});
