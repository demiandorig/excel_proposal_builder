"""Check ReachPlanService access and list live YouTube planning formats.

Run from the repository root after setting the Google Ads secrets. This is a
read-only operator check; it makes no campaigns, forecasts, or database writes.
"""
from __future__ import annotations

import argparse
import os
import sys


_REQUIRED = (
    "GOOGLE_ADS_DEVELOPER_TOKEN",
    "GOOGLE_ADS_CLIENT_ID",
    "GOOGLE_ADS_CLIENT_SECRET",
    "GOOGLE_ADS_REFRESH_TOKEN",
    "GOOGLE_ADS_LOGIN_CUSTOMER_ID",
)


def _client():
    missing = [name for name in _REQUIRED if not os.environ.get(name)]
    if missing:
        raise RuntimeError("Missing Replit Secrets: " + ", ".join(missing))
    try:
        from google.ads.googleads.client import GoogleAdsClient
    except ImportError as exc:
        raise RuntimeError("Install the optional Google Ads client: pip install google-ads") from exc
    return GoogleAdsClient.load_from_dict({
        "developer_token": os.environ["GOOGLE_ADS_DEVELOPER_TOKEN"],
        "client_id": os.environ["GOOGLE_ADS_CLIENT_ID"],
        "client_secret": os.environ["GOOGLE_ADS_CLIENT_SECRET"],
        "refresh_token": os.environ["GOOGLE_ADS_REFRESH_TOKEN"],
        "login_customer_id": os.environ["GOOGLE_ADS_LOGIN_CUSTOMER_ID"],
        "use_proto_plus": True,
    })


def probe(location_id: str) -> None:
    service = _client().get_service("ReachPlanService")
    print("ReachPlanService locations (first 15):")
    locations = service.list_plannable_locations()
    for location in list(locations.plannable_locations)[:15]:
        print(f"  {location.id}\t{location.name}\tparent={location.parent_country_id}")
    if location_id not in {str(location.id) for location in locations.plannable_locations}:
        raise RuntimeError(f"Location {location_id} is not in the plannable location list.")
    products = service.list_plannable_products(plannable_location_id=location_id)
    print(f"Plannable products for location {location_id}:")
    for product in products.product_metadata:
        print(f"  {product.plannable_product_code}\t{product.plannable_product_name}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--location-id", default="2840", help="Google planning location ID (default: US 2840)")
    args = parser.parse_args()
    try:
        probe(args.location_id)
    except Exception as exc:
        # Do not print the library's raw exception: some API clients include
        # request metadata in it. Authentication and allowlist failures are
        # actionable without logging credential-bearing details.
        if isinstance(exc, RuntimeError):
            print(str(exc), file=sys.stderr)
        else:
            print("ReachPlanService probe failed. Verify OAuth/customer access and Google's ReachPlanService allowlist.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
