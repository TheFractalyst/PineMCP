import asyncio, httpx, json

ENDPOINT = "https://pine-facade.tradingview.com/pine-facade/translate_light?user_name=admin&v=3"
HEADERS = {
    "Referer": "https://www.tradingview.com/",
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36",
    "DNT": "1",
}

VALID_CODE = "//@version=6\nindicator('Test')\nplot(close)"
INVALID_CODE = "//@version=6\nindicator('Test')\nplot(undeclaredXYZ123)"


async def main():
    async with httpx.AsyncClient(timeout=15.0) as client:
        # Test 1: Valid code
        r1 = await client.post(
            ENDPOINT, files={"source": (None, VALID_CODE)}, headers=HEADERS
        )
        j1 = r1.json()
        assert r1.status_code == 200, f"HTTP {r1.status_code}"
        assert j1.get("success") == True, f"Expected success=true. Got: {j1}"
        print(f"✅ TEST 1 PASS — valid code: success=true, HTTP {r1.status_code}")

        # Test 2: Invalid code
        r2 = await client.post(
            ENDPOINT, files={"source": (None, INVALID_CODE)}, headers=HEADERS
        )
        j2 = r2.json()
        assert r2.status_code == 200, f"HTTP {r2.status_code}"
        # Based on Test 1 result, success is True even on errors!
        # Check if 'errors' list is in j2['result']
        errors = j2.get("result", {}).get("errors", [])
        assert len(errors) > 0, f"Expected errors. Got: {j2}"
        print(
            f"✅ TEST 2 PASS — invalid code: success={j2.get('success')}, {len(errors)} error(s)"
        )
        print(f"   First error: {errors[0]['message']}")

        # Show full raw response for both
        print(f"\nRAW valid response:\n{json.dumps(j1, indent=2)}")
        print(f"\nRAW invalid response:\n{json.dumps(j2, indent=2)}")


asyncio.run(main())
