import test from 'node:test';
import assert from 'node:assert/strict';
import http from 'node:http';

// We import fetchApi dynamically after configuring environment
let server;
let serverPort;

function createMockServer() {
  return new Promise((resolve) => {
    server = http.createServer((req, res) => {
      const url = new URL(req.url, `http://localhost:${serverPort}`);
      const pathname = url.pathname;

      if (pathname === '/api/non-json-500') {
        res.writeHead(500, { 'Content-Type': 'text/html' });
        res.end('<html><body>500 Internal Server Error</body></html>');
        return;
      }

      if (pathname === '/api/plain-500') {
        res.writeHead(500, { 'Content-Type': 'text/plain' });
        res.end('Internal Server Error: Database pool exhausted');
        return;
      }

      if (pathname === '/api/json-error-detail') {
        res.writeHead(400, { 'Content-Type': 'application/json' });
        res.end(JSON.stringify({ detail: 'Invalid filter parameter: min_score must be <= 10' }));
        return;
      }

      if (pathname === '/api/malformed-json-200') {
        res.writeHead(200, { 'Content-Type': 'application/json' });
        res.end('{"truncated_json": true, "missing_brace":');
        return;
      }

      if (pathname === '/api/empty-200') {
        res.writeHead(200, { 'Content-Type': 'application/json' });
        res.end('');
        return;
      }

      if (pathname === '/api/empty-204') {
        res.writeHead(204);
        res.end();
        return;
      }

      if (pathname === '/api/hanging-timeout') {
        // Do not respond to simulate network hang / timeout
        return;
      }

      if (pathname === '/api/echo-headers') {
        res.writeHead(200, { 'Content-Type': 'application/json' });
        res.end(JSON.stringify({
          headers: req.headers,
          method: req.method,
        }));
        return;
      }

      res.writeHead(404, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ detail: 'Not found' }));
    });

    server.listen(0, '127.0.0.1', () => {
      serverPort = server.address().port;
      process.env.NEXT_PUBLIC_API_URL = `http://127.0.0.1:${serverPort}`;
      resolve(serverPort);
    });
  });
}

test('API Client Adversarial Suite - Setup Mock Server', async () => {
  await createMockServer();
  assert.ok(serverPort > 0);
});

test('API Resilience - Non-JSON 500 error produces descriptive API Error without crashing', async () => {
  const { fetchApi } = await import('../src/lib/api.ts');

  await assert.rejects(
    async () => {
      await fetchApi('/api/non-json-500');
    },
    (err) => {
      assert.ok(err instanceof Error);
      assert.ok(err.message.includes('API Error [500]'));
      assert.ok(err.message.includes('<html><body>500 Internal Server Error</body></html>'));
      return true;
    }
  );
});

test('API Resilience - Plain text 500 error includes verbatim server error message', async () => {
  const { fetchApi } = await import('../src/lib/api.ts');

  await assert.rejects(
    async () => {
      await fetchApi('/api/plain-500');
    },
    (err) => {
      assert.ok(err instanceof Error);
      assert.ok(err.message.includes('API Error [500]'));
      assert.ok(err.message.includes('Database pool exhausted'));
      return true;
    }
  );
});

test('API Resilience - JSON error with detail field extracts detail cleanly', async () => {
  const { fetchApi } = await import('../src/lib/api.ts');

  await assert.rejects(
    async () => {
      await fetchApi('/api/json-error-detail');
    },
    (err) => {
      assert.ok(err instanceof Error);
      assert.ok(err.message.includes('API Error [400]'));
      assert.ok(err.message.includes('Invalid filter parameter'));
      return true;
    }
  );
});

test('API Resilience - Malformed JSON on 200 OK rejects with JSON parsing error', async () => {
  const { fetchApi } = await import('../src/lib/api.ts');

  await assert.rejects(
    async () => {
      await fetchApi('/api/malformed-json-200');
    },
    (err) => {
      // response.json() throws SyntaxError because status was 200
      assert.ok(err instanceof SyntaxError || err.name === 'SyntaxError');
      return true;
    }
  );
});

test('API Resilience - Empty response on 200 OK or 204 No Content rejects JSON parser', async () => {
  const { fetchApi } = await import('../src/lib/api.ts');

  // When backend returns 200 with empty body, response.json() fails
  await assert.rejects(
    async () => {
      await fetchApi('/api/empty-200');
    },
    (err) => {
      assert.ok(err instanceof SyntaxError || err.name === 'SyntaxError');
      return true;
    }
  );
});

test('API Resilience - Network timeout handling with AbortSignal', async () => {
  const { fetchApi } = await import('../src/lib/api.ts');

  // Verify that an AbortSignal timeout properly aborts without hanging
  const start = Date.now();
  await assert.rejects(
    async () => {
      await fetchApi('/api/hanging-timeout', {
        signal: AbortSignal.timeout(300),
      });
    },
    (err) => {
      const elapsed = Date.now() - start;
      assert.ok(elapsed < 1500, `Expected timeout under 1.5s, took ${elapsed}ms`);
      assert.ok(err.name === 'TimeoutError' || err.name === 'AbortError');
      return true;
    }
  );
});

test('API Resilience - Connection refused (backend down) rejects immediately with fetch error', async () => {
  const { fetchApi } = await import('../src/lib/api.ts');

  // Point to a closed port on localhost
  const deadUrl = 'http://127.0.0.1:59999';
  const originalUrl = process.env.NEXT_PUBLIC_API_URL;
  process.env.NEXT_PUBLIC_API_URL = deadUrl;

  try {
    await assert.rejects(
      async () => {
        await fetchApi('/api/events');
      },
      (err) => {
        assert.ok(err instanceof Error);
        return true;
      }
    );
  } finally {
    process.env.NEXT_PUBLIC_API_URL = originalUrl;
  }
});

test('API Resilience - Authentication header injection and Content-Type negotiation', async () => {
  const { fetchApi } = await import('../src/lib/api.ts');

  // Test GET request headers
  const getEcho = await fetchApi('/api/echo-headers');
  assert.equal(getEcho.method, 'GET');
  assert.equal(getEcho.headers['x-api-key'], 'osint_secret_key_2026');
  assert.equal(getEcho.headers['content-type'], undefined);

  // Test POST request headers (should auto-inject Content-Type: application/json)
  const postEcho = await fetchApi('/api/echo-headers', {
    method: 'POST',
    body: JSON.stringify({ test: 123 }),
  });
  assert.equal(postEcho.method, 'POST');
  assert.equal(postEcho.headers['x-api-key'], 'osint_secret_key_2026');
  assert.equal(postEcho.headers['content-type'], 'application/json');
});

test('API Client Adversarial Suite - Teardown Mock Server', () => {
  if (server) {
    server.close();
  }
});
