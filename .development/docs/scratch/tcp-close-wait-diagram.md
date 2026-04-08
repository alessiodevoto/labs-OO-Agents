# TCP CLOSE_WAIT State - Detailed Diagram

## Normal TCP Connection Closure (Clean Close)

```
CLIENT (httpx)                           SERVER (NVIDIA API)
    |                                           |
    |  1. [ESTABLISHED] ----Request----->  [ESTABLISHED]
    |                                           |
    |  2. [ESTABLISHED] <----Response----  [ESTABLISHED]
    |                                           |
    |  3. [FIN_WAIT_1]  ----FIN--------->  [CLOSE_WAIT]
    |     "I'm done"                            |
    |                                           |
    |  4. [FIN_WAIT_2]  <----ACK---------  [CLOSE_WAIT]
    |                       "OK, got it"        |
    |                                           |
    |  5. [TIME_WAIT]   <----FIN---------  [LAST_ACK]
    |                     "I'm done too"        |
    |                                           |
    |  6. [TIME_WAIT]   ----ACK--------->  [CLOSED]
    |                                           |
    |  7. [CLOSED]                         [CLOSED]
    |     Connection fully closed          Connection closed
```

## What's Happening with NVIDIA API (Broken Close)

```
CLIENT (httpx)                           SERVER (NVIDIA API)
    |                                           |
    |  1. [ESTABLISHED] ----Request----->  [ESTABLISHED]
    |                                           |
    |  2. [ESTABLISHED] <----Response---- [ESTABLISHED]
    |     Receives data...                     |
    |                                           |
    |  3. [ESTABLISHED]                    [???]
    |     Still waiting for                Server encounters error
    |     more data...                     or timeout internally
    |                                           |
    |  4. [CLOSE_WAIT]  <----FIN---------  [FIN_WAIT_2]
    |     "Server sent FIN"                "Told client I'm done"
    |     "But I never got                      |
    |      complete HTTP response!"             |
    |                                           |
    |     *** STUCK HERE ***               [FIN_WAIT_2]
    |     httpx is blocked                 Server waiting for
    |     reading from socket              client's FIN
    |                                           |
    |     Client should close(),           Eventually times out
    |     but httpx is in the              and goes to CLOSED
    |     middle of recv() call                 |
    |     waiting for data!                [CLOSED]
    |
    |  [CLOSE_WAIT]
    |  Connection zombie:
    |  - Not fully ESTABLISHED (server sent FIN)
    |  - Not CLOSED (client hasn't called close())
    |  - recv() blocking forever waiting for data that will never come
    |  - httpx read timeout doesn't fire (it only fires on ESTABLISHED connections)
```

## Why httpx Connection Pooling Makes It Worse

```
Request 1:
CLIENT -----> [Connection 1: ESTABLISHED] -----> SERVER
              (in pool, ready for reuse)

SERVER sends FIN without complete response:
CLIENT <----- [Connection 1: CLOSE_WAIT] <------ SERVER (FIN)
              *** ZOMBIE CONNECTION ***
              - Still in the pool!
              - httpx thinks it's reusable
              - Actually completely dead

Request 2 tries to reuse Connection 1:
CLIENT -----> [Connection 1: CLOSE_WAIT]
              httpx.send() writes data to socket ✓
              httpx.recv() blocks forever waiting for response ✗
              *** HANG ***

Meanwhile, new requests create more connections:
CLIENT -----> [Connection 2: ESTABLISHED] -----> SERVER
CLIENT -----> [Connection 3: ESTABLISHED] -----> SERVER
...

Each one eventually enters CLOSE_WAIT:
[Connection 1: CLOSE_WAIT] (zombie, blocking recv)
[Connection 2: CLOSE_WAIT] (zombie, blocking recv)
[Connection 3: CLOSE_WAIT] (zombie, blocking recv)
[Connection 4: CLOSE_WAIT] (zombie, blocking recv)
[Connection 5: CLOSE_WAIT] (zombie, blocking recv)

All concurrent tasks blocked on zombie connections!
```

## Why Our Fixes Didn't Work

### Attempt 1: httpx.Timeout(read=60.0)
```
httpx read timeout only applies to ESTABLISHED connections:

[ESTABLISHED] --recv()--> [timeout after 60s of no data] ✓ Works

[CLOSE_WAIT] --recv()--> [???]
   ^^^
   Not ESTABLISHED anymore! Server sent FIN.
   httpx sees this as "connection closing" state
   Timeout logic doesn't apply ✗ Doesn't fire
```

### Attempt 2: asyncio.wait_for()
```
asyncio.wait_for(litellm.acompletion(), timeout=90.0)
                        |
                        v
                 httpx.recv()
                        |
                        v
                 socket.recv()  <-- OS-level blocking system call
                        |
                        *** STUCK IN KERNEL ***

asyncio.wait_for() can only cancel cooperative async operations.
It CANNOT interrupt blocking system calls like socket.recv().
The call is literally stuck in the OS kernel waiting for data.
```

### Attempt 3: Disable Connection Pooling (max_keepalive_connections=0)
```
Problem: We only replaced the client ONCE at initialization:

CompletionClient.__init__():
    litellm.module_level_aclient.client = httpx.AsyncClient(
        limits=Limits(max_keepalive_connections=0)
    )

But during runtime:
    - LiteLLM might create NEW httpx clients internally
    - OR our client replacement happens too early (before LiteLLM initializes)
    - OR LiteLLM replaces the client later
    - Result: Our configuration is ignored/overwritten

Evidence: CLOSE_WAIT connections still accumulate with pooling behavior
```

## The Real Problem: httpx recv() Blocks in CLOSE_WAIT

```python
# Simplified httpx internal flow:

async def _receive_response_data():
    while True:
        # This recv() call blocks forever in CLOSE_WAIT state!
        chunk = await self._network_stream.read(READ_NUM_BYTES)

        if not chunk:
            break  # This SHOULD happen when FIN received

        yield chunk

# What actually happens in CLOSE_WAIT:
# 1. Server sent FIN (no more data coming)
# 2. But httpx doesn't realize this immediately
# 3. recv() is called, enters kernel
# 4. Kernel says: "connection in CLOSE_WAIT, waiting for more data"
# 5. recv() blocks forever
# 6. No timeout fires because we're not in ESTABLISHED state
```

## Why This Happens with NVIDIA API Specifically

```
Normal API:
    Client: POST /v1/chat/completions
    Server: HTTP/1.1 200 OK\r\n
            Content-Length: 1234\r\n
            \r\n
            [1234 bytes of JSON]
            [FIN] ← Sends FIN AFTER complete response

NVIDIA API Bug:
    Client: POST /v1/chat/completions
    Server: HTTP/1.1 200 OK\r\n
            Transfer-Encoding: chunked\r\n
            \r\n
            [some chunks...]
            [FIN] ← Sends FIN BEFORE complete response!

    Client receives FIN but:
        - Chunked encoding not finished (no final "0\r\n\r\n")
        - No complete JSON response
        - httpx still waiting for more chunks
        - recv() blocks in CLOSE_WAIT forever
```

## Real Example from NVIDIA Internal API (2026-01-13)

### What We Observed

**Test Run Details:**
- Model: `nvidia/qwen/qwen3-next-80b-a3b-instruct`
- Endpoint: `https://inference-api.nvidia.com/v1` (internal)
- Concurrent tasks: 5
- Results: Both Qwen and GPT processes hung with CLOSE_WAIT

**CLOSE_WAIT Connections (from lsof):**
```bash
$ lsof -p 28272 | grep CLOSE_WAIT
python3.1 28272 rcabral 16u IPv4 ... TCP dhcp-10-21-84-104.nvidia.com:63894->10.48.202.181:https (CLOSE_WAIT)
python3.1 28272 rcabral 17u IPv4 ... TCP dhcp-10-21-84-104.nvidia.com:63895->10.48.202.125:https (CLOSE_WAIT)
python3.1 28272 rcabral 18u IPv4 ... TCP dhcp-10-21-84-104.nvidia.com:63896->10.48.203.203:https (CLOSE_WAIT)
python3.1 28272 rcabral 19u IPv4 ... TCP dhcp-10-21-84-104.nvidia.com:63897->10.48.202.181:https (CLOSE_WAIT)
python3.1 28272 rcabral 20u IPv4 ... TCP dhcp-10-21-84-104.nvidia.com:63898->10.48.203.203:https (CLOSE_WAIT)
python3.1 28272 rcabral 23u IPv4 ... TCP dhcp-10-21-84-104.nvidia.com:64897->104.18.26.120:http (CLOSE_WAIT)
```

**Timeline:**
- 13:15 - Processes started with `max_keepalive_connections=0` fix
- 13:24 - Qwen: 1 CLOSE_WAIT, still progressing (144 tasks)
- 13:28 - Qwen: 6 CLOSE_WAIT, STALLED at 144 tasks
- 13:45 - Qwen: 6 CLOSE_WAIT, GPT: 5 CLOSE_WAIT, BOTH STALLED
- Process never recovered - manual kill required

### What Actually Happened (Reconstructed)

Based on the CLOSE_WAIT connections to NVIDIA internal endpoints (10.48.202.x, 10.48.203.x):

```
13:16 - Request to nvidia/qwen/qwen3-next-80b-a3b-instruct:

CLIENT (httpx)                                    NVIDIA API (10.48.202.181)
    |                                                      |
    | POST /v1/chat/completions HTTP/1.1                  |
    | Host: inference-api.nvidia.com                      |
    | Content-Type: application/json                      |
    | {"model": "nvidia/qwen/qwen3-next-80b-a3b-instruct",|
    |  "messages": [...], ...}                            |
    |----------------------------------------------------->|
    |                                                      |
    | HTTP/1.1 200 OK                                     |
    | Transfer-Encoding: chunked                          |
    | <-----------------------------------------------------
    |                                                      |
    | Chunk 1: {"id":"chatcmpl-xxx","object":"chat.com    |
    | <-----------------------------------------------------
    |                                                      |
    | Chunk 2: pletion","choices":[{"index":0,"mess       |
    | <-----------------------------------------------------
    |                                                      |
    | Chunk 3: age":{"role":"assistant","content":"       |
    | <-----------------------------------------------------
    |                                                      |
    |           *** SERVER ENCOUNTERS ERROR ***           |
    |           (timeout, OOM, rate limit, etc)           |
    |                                                      |
    | [FIN]                                               |
    | <-----------------------------------------------------
    | ^^^ Server closes connection WITHOUT:                |
    |     - Sending final chunk terminator (0\r\n\r\n)    |
    |     - Completing the JSON response                   |
    |     - Sending HTTP trailers                          |
    |                                                      |
    | [ACK FIN]                                           |
    |----------------------------------------------------->|
    |                                                      |
    | Connection enters CLOSE_WAIT                        |
    |                                                      |
    | httpx code:                                         |
    |   chunk = await stream.read()                       |
    |           ^^^ Expecting more data                   |
    |           ^^^ socket.recv() called                  |
    |           ^^^ BLOCKS FOREVER in kernel              |
    |                                                      |
    | No timeout fires:                                   |
    | - Not ESTABLISHED anymore (got FIN)                 |
    | - asyncio.wait_for() can't interrupt recv()         |
    | - Connection pooling disabled but doesn't help      |
    |                                                      |
    | *** HUNG FOREVER ***                                |
```

### Actual Incomplete Response Example

Based on chunked transfer encoding, the client likely received something like:

```
HTTP/1.1 200 OK
Transfer-Encoding: chunked
Content-Type: application/json

2f
{"id":"chatcmpl-abc123","object":"chat.completion","created":1768306674
45
,"model":"nvidia/qwen/qwen3-next-80b-a3b-instruct","choices":[{"index":0,"message":
3a
{"role":"assistant","content":"import itertools\nimport string\nimport pandas
[FIN] ← Connection closed here!

Expected but never received:
\r\n
0
\r\n
\r\n
```

**What httpx is waiting for:**
- The final chunk size (`0\r\n\r\n`) to indicate end of chunked encoding
- The closing `}` characters for the JSON response
- OR an error from socket.recv() that would allow it to proceed

**What it gets instead:**
- FIN packet (connection closing)
- But socket.recv() doesn't return an error
- It just blocks forever waiting for data that will never come
- The TCP state is CLOSE_WAIT (waiting for client to close)
- But httpx can't close because it's stuck in recv()

### Evidence from Multiple Runs

All runs with NVIDIA internal API show same pattern:
- **Run 1 (PID 3083)**: 5 CLOSE_WAIT after 9 minutes, 145/1140 tasks
- **Run 2 (PID 25398)**: Process disappeared, 140/1140 tasks
- **Run 3 (PID 94424)**: 5 CLOSE_WAIT after 16 minutes, 143/1140 tasks
- **Run 4 (PID 28272)**: 6 CLOSE_WAIT after 13 minutes, 144/1140 tasks

**Consistent pattern:**
- Always stalls around 140-145 tasks (~12-13%)
- Always exactly 5-6 CLOSE_WAIT connections (matching concurrency)
- Always requires manual kill to recover
- OpenAI endpoint (GPT) experiences same issue, suggesting NVIDIA infrastructure problem

## Possible Solutions

### Option 1: Streaming with Timeout (Best)
```python
async def _make_call_with_streaming():
    response = await litellm.acompletion(stream=True, ...)
    chunks = []
    last_chunk_time = time.time()

    async for chunk in response:
        chunks.append(chunk)
        last_chunk_time = time.time()

        # Check timeout between chunks
        if time.time() - last_chunk_time > 30.0:
            raise StreamTimeoutError()

    return merge_chunks(chunks)
```

### Option 2: Global httpx Monkey-Patch
```python
# Patch httpx.AsyncClient class itself
import httpx
_original_init = httpx.AsyncClient.__init__

def _patched_init(self, *args, **kwargs):
    kwargs.setdefault('limits', httpx.Limits(max_keepalive_connections=0))
    return _original_init(self, *args, **kwargs)

httpx.AsyncClient.__init__ = _patched_init
```

### Option 3: Thread Pool with Timeout (Nuclear Option)
```python
async def _make_call():
    # Run sync litellm.completion() in thread
    # Threads CAN be forcibly killed
    result = await asyncio.wait_for(
        asyncio.to_thread(litellm.completion, **params),
        timeout=90.0
    )
```

### Option 4: External Watchdog Process
```python
# Separate process monitors for CLOSE_WAIT
# Kills hung processes automatically
while True:
    if get_close_wait_count(pid) >= 5:
        os.kill(pid, signal.SIGKILL)
```
