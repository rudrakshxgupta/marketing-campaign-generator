"""Verify the Foundry connection without generating a single image.

Sends a deliberately invalid request. The service rejects it before any work
happens, so the call costs nothing -- but the status code still tells us
everything we need:

    401  the key is wrong or expired
    404  the deployment name does not exist on this resource
    400  auth is fine and the deployment exists  <- this is success

Run it before the real probe, so a typo in the endpoint costs a round trip
rather than three images.

    python scripts/check_connection.py                  # try the usual names
    python scripts/check_connection.py my-deployment    # try a specific one
"""

from __future__ import annotations

import os
import sys

import httpx

#: Names people commonly give these deployments. The portal's default is the
#: model name itself, which is why it leads.
CANDIDATES = [
    "MAI-Image-2.6",
    "mai-image-26",
    "mai-image-2.6",
    "MAI-Image-2.6-Flash",
    "mai-image-26-flash",
]


def main() -> int:
    endpoint = os.environ.get("FOUNDRY_ENDPOINT", "").rstrip("/")
    key = os.environ.get("FOUNDRY_API_KEY", "")

    if not endpoint:
        print("FOUNDRY_ENDPOINT is not set.")
        print("It is the host root, e.g. https://<resource>.services.ai.azure.com")
        print("-- not the /api/projects/... URL the portal shows for a project.")
        return 1
    if not key:
        print("FOUNDRY_API_KEY is not set (and no az login available).")
        return 1

    if "/api/projects/" in endpoint:
        host = endpoint.split("/api/projects/")[0]
        print(f"note: that looks like a project URL. Using the host root instead:\n  {host}\n")
        endpoint = host

    url = f"{endpoint}/mai/v1/images/generations"
    print(f"endpoint   {endpoint}")
    print(f"key        ...{key[-6:]} ({len(key)} chars)\n")

    names = sys.argv[1:] or CANDIDATES
    found: list[str] = []

    with httpx.Client(timeout=45) as client:
        for name in names:
            # width=1 is below the 768 floor, so the service refuses it before
            # generating anything. No image, no charge.
            try:
                response = client.post(
                    url,
                    headers={"api-key": key, "Content-Type": "application/json"},
                    json={"model": name, "prompt": "connectivity check",
                          "width": 1, "height": 1},
                )
            except httpx.RequestError as exc:
                print(f"  {name:<24} network error: {exc}")
                print("\nCheck the endpoint hostname is right.")
                return 1

            status = response.status_code
            if status == 401:
                print(f"  {name:<24} 401 - key rejected")
                print("\nThe key is wrong, expired, or belongs to another resource.")
                print("Azure Portal -> your resource -> Keys and Endpoint.")
                return 1
            if status == 404:
                print(f"  {name:<24} 404 - no such deployment")
            elif status in (400, 422):
                print(f"  {name:<24} {status} - EXISTS, auth OK")
                found.append(name)
            else:
                body = response.text[:160].replace("\n", " ")
                print(f"  {name:<24} {status} - {body}")
                if status == 200:
                    # Should not happen with an illegal size, but if it does we
                    # just spent a generation and should say so.
                    print("    (unexpected: that request was billable)")
                    found.append(name)

    print()
    if found:
        print(f"Connected. Usable deployment(s): {', '.join(found)}")
        print(f"\nSet in .env:\n  MAI_MOCK=0\n  FOUNDRY_ENDPOINT={endpoint}"
              f"\n  MAI_IMAGE_DEPLOYMENT={found[0]}")
        print("\nZero images were generated.")
        return 0

    print("Auth works but none of those deployment names exist.")
    print("Find the real name in the Foundry portal under Deployments,")
    print("then re-run:  python scripts/check_connection.py <name>")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
