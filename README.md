# Sequential download speed test

Small Python CLI that downloads the same HTTP(S) resource ten times in sequence, waits for each response body to finish, and reports the average attempt time, total downloaded data, and aggregate download speed.

## Requirements

- Python 3.10 or newer
- No third-party packages
- A reachable HTTP(S) URL that returns a downloadable file (a larger image or archive gives a more useful measurement)

## Run

```bash
python speed_test.py "https://example.com/large-image.jpg"
```

Each of the ten requests is made only after the previous response body has been fully read. The body is streamed in 64 KiB chunks, so the script does not keep the whole file in memory. Per-request timeout defaults to 30 seconds and can be changed with `--timeout`:

```bash
python speed_test.py "https://example.com/large-image.jpg" --timeout 60
```

## Units and calculation

- Bytes are counted from response bodies. `KB` and `MB` use decimal units: 1 KB = 1,000 bytes and 1 MB = 1,000,000 bytes.
- Average attempt time is the arithmetic mean of all ten attempts, including failed attempts.
- Aggregate speed is total received body bytes × 8 ÷ total wall-clock test time. It includes connection/setup time and any failed attempts, which is why the report also shows the success/error counts. The displayed `Mbit/s` uses 1,000,000 bits per second; `MB/s` is `Mbit/s ÷ 8`.
- If an attempt fails, the script still proceeds to the remaining requests, reports the error, and exits with code 1. A clean run exits with code 0.
- The console prints only the target host, not the URL path or query string, to avoid echoing tokens embedded in a URL.

## Tests

Run the standard-library test suite from this directory:

```bash
python -m unittest discover -s tests -v
```

The tests use a local HTTP server; they do not need an external network connection.

