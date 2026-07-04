const fs = require("fs");
const path = require("path");
const crypto = require("crypto");
const fernet = require("fernet");
const axios = require("axios");
const { Client } = require("pg");

const repoRoot = path.resolve(__dirname, "../..");
require("dotenv").config({ path: path.join(repoRoot, ".env") });

const DEFAULT_SYMBOLS = ["BSXOPT02JUL2678400CE", "BSXOPT02JUL2677900CE"];

function parseArgs() {
  const args = process.argv.slice(2);
  const out = {
    userId: 1,
    symbols: DEFAULT_SYMBOLS.join(","),
    durationSec: 300,
    perCallSleepSec: 0.4,
    logDir: "shoonya_sdk_test/python/logs",
    baseUrl: "https://api.shoonya.com/NorenWClientAPI/",
    route: "/GetQuotes",
    timeoutMs: 7000,
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
    } else if (arg === "--base-url" && next) {
      out.baseUrl = next;
      i += 1;
    } else if (arg === "--route" && next) {
      out.route = next;
      i += 1;
    } else if (arg === "--timeout-ms" && next) {
      out.timeoutMs = Number(next);
      i += 1;
    }
  }

  return out;
}

function normalizeDbUrl(rawDbUrl) {
  if (!rawDbUrl) {
    throw new Error("DATABASE_URL is missing");
  }
  return rawDbUrl
    .replace("postgresql+asyncpg://", "postgresql://")
    .replace("postgresql+psycopg://", "postgresql://");
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

function normalizeBaseUrl(url) {
  return String(url || "").replace(/\/+$/, "");
}

async function postNoren(baseUrl, route, jData, jKey, timeoutMs) {
  const normalizedBase = normalizeBaseUrl(baseUrl);
  const normalizedRoute = String(route || "/GetQuotes").startsWith("/")
    ? String(route || "/GetQuotes")
    : `/${String(route || "GetQuotes")}`;
  const url = `${normalizedBase}${normalizedRoute}`;

  // Shoonya SDK posts a raw form body string without URL-encoding jData.
  const body = `jData=${JSON.stringify(jData)}&jKey=${jKey}`;

  const response = await axios.post(url, body, {
    headers: {
      "Content-Type": "application/x-www-form-urlencoded",
    },
    timeout: timeoutMs,
    validateStatus: () => true,
  });

  return {
    url,
    status: response.status,
    data: response.data,
  };
}

function validateResponse(reqSymbol, reqExchange, reqToken, httpResult) {
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
    http_status: httpResult ? httpResult.status : null,
    error: null,
  };

  if (!httpResult || httpResult.status !== 200) {
    info.classification = "http_error";
    info.error = `HTTP ${httpResult ? httpResult.status : "unknown"}`;
    return info;
  }

  const payload = httpResult.data;
  if (!payload || typeof payload !== "object") {
    info.classification = "invalid_payload";
    info.error = "API returned non-object payload";
    return info;
  }

  info.resp_stat = payload.stat ?? null;
  info.resp_token = payload.token != null ? String(payload.token) : null;
  info.ltp = payload.lp ?? null;

  if (payload.stat !== "Ok") {
    info.classification = "api_not_ok";
    info.error = payload.emsg || "Unknown API error";
    return info;
  }

  info.ok = true;
  info.token_match = String(payload.token) === String(reqToken);

  if (info.token_match) {
    info.classification = "ok";
  } else if (reqExchange === "BFO" && String(payload.token) === "1") {
    info.classification = "bfo_option_returned_sensex_token";
  } else {
    info.classification = "token_mismatch";
  }

  return info;
}

async function runValidationLoop({
  uid,
  jKey,
  instruments,
  durationSec,
  perCallSleepSec,
  baseUrl,
  route,
  timeoutMs,
  jsonlFilePath,
  logger,
}) {
  const started = Date.now();
  const counts = {
    total_calls: 0,
    ok: 0,
    api_not_ok: 0,
    http_error: 0,
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

        counts.total_calls += 1;

        let result;
        try {
          const httpResult = await postNoren(
            baseUrl,
            route,
            {
              uid,
              exch: ins.exchange,
              token: ins.token,
            },
            jKey,
            timeoutMs,
          );
          result = validateResponse(ins.symbol, ins.exchange, ins.token, httpResult);
        } catch (error) {
          result = {
            req_symbol: ins.symbol,
            req_exchange: ins.exchange,
            req_token: ins.token,
            ok: false,
            token_match: false,
            classification: "exception",
            ltp: null,
            resp_token: null,
            resp_stat: null,
            http_status: null,
            error: String(error && error.message ? error.message : error),
          };
        }

        if (result.ok) counts.ok += 1;
        if (result.classification === "api_not_ok") counts.api_not_ok += 1;
        if (result.classification === "http_error") counts.http_error += 1;
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
          logger.info(`OK ${ins.symbol}/${ins.exchange} token=${ins.token} ltp=${result.ltp}`);
        } else {
          logger.warn(
            `ISSUE ${result.classification} ${ins.symbol}/${ins.exchange} req_token=${ins.token} resp_token=${result.resp_token} stat=${result.resp_stat} http=${result.http_status} err=${result.error}`,
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

  const dbUrl = normalizeDbUrl(
    process.env.DATABASE_URL ||
      process.env.database_url ||
      "postgresql+asyncpg://postgres:123456@localhost:5432/openbull",
  );

  const symbols = args.symbols
    .split(",")
    .map((s) => s.trim())
    .filter(Boolean);

  const resolvedSymbols = symbols.length > 0 ? symbols : DEFAULT_SYMBOLS;

  const outputDir = path.resolve(repoRoot, args.logDir);
  fs.mkdirSync(outputDir, { recursive: true });

  const stamp = new Date().toISOString().replace(/[-:]/g, "").replace(/\.\d+Z$/, "").replace("T", "_");
  const logFilePath = path.join(outputDir, `getquotes_direct_api_validation_${stamp}.log`);
  const jsonlFilePath = path.join(outputDir, `getquotes_direct_api_validation_${stamp}.jsonl`);

  const logger = createLogger(logFilePath);
  const dbClient = new Client({ connectionString: dbUrl });

  try {
    await dbClient.connect();

    const authToken = await loadLiveAuth(dbClient, args.userId, encryptionPepper);
    const [uid, jKey] = parseAuthToken(authToken);
    if (!uid || !jKey) {
      throw new Error("Invalid Shoonya auth token format in DB");
    }

    const instruments = await resolveInstruments(dbClient, resolvedSymbols);
    const requested = new Set(resolvedSymbols);
    const found = new Set(instruments.map((x) => x.symbol));
    const missing = [...requested].filter((s) => !found.has(s)).sort();

    logger.info("Starting Shoonya GetQuotes validation (Direct HTTP API)");
    logger.info(`Base URL=${normalizeBaseUrl(args.baseUrl)} Route=${args.route}`);
    logger.info(`Duration=${args.durationSec}s per_call_sleep=${args.perCallSleepSec.toFixed(3)}s timeout=${args.timeoutMs}ms`);
    logger.info("Request format: application/x-www-form-urlencoded jData=<json>&jKey=<session>");
    logger.info(`Resolved instruments=${JSON.stringify(instruments)}`);
    if (missing.length) {
      logger.warn(`Symbols not found in symtoken table: ${JSON.stringify(missing)}`);
    }

    const counts = await runValidationLoop({
      uid,
      jKey,
      instruments,
      durationSec: args.durationSec,
      perCallSleepSec: args.perCallSleepSec,
      baseUrl: args.baseUrl,
      route: args.route,
      timeoutMs: args.timeoutMs,
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
