#!/usr/bin/env python3
"""AdsCrawl persistent lifecycle CLI; only safe summary fields reach stdout."""

import argparse
import http.client
import ipaddress
import json
import math
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request


SAFE_CODES = frozenset("""
PROXY_REQUIRED INVALID_PROXY COUNTRY_PROXY_CONFLICT INVALID_FINGERPRINT_SETTINGS
PAID_PLAN_REQUIRED INSUFFICIENT_CREDITS CLOUD_BROWSER_CONCURRENCY_LIMIT
CDP_SESSION_STARTING CLUSTER_NO_CAPACITY DYNAMIC_PROXY_NOT_CONFIGURED
MANAGED_PROXY_UNAVAILABLE CLOUD_RUNTIME_CONFIG_INVALID CLOUD_RUNTIME_TIMEOUT
CLOUD_RUNTIME_UNREACHABLE CLOUD_RUNTIME_AUTH_FAILED CLOUD_RUNTIME_NOT_FOUND
CLOUD_RUNTIME_HTTP_ERROR CLOUD_RUNTIME_INVALID_RESPONSE CLOUD_RUNTIME_CONTROLLER_FAILED
""".split())
STATES = {"starting", "running", "stopping", "stopped"}


class LifecycleError(Exception):
    """A diagnostic constructed locally without secret-bearing server data."""


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None  # Never forward X-API-Key to a redirect destination.


def origin(value):
    try:
        url = urllib.parse.urlsplit(value)
        loopback = url.hostname == "localhost"
        if not loopback:
            try:
                loopback = ipaddress.ip_address(url.hostname).is_loopback
            except ValueError:
                pass
        valid = (
            url.hostname and not url.username and not url.password
            and not url.query and not url.fragment and url.path in ("", "/")
            and (url.scheme == "https" or (url.scheme == "http" and loopback))
        )
        if url.port is not None and not 1 <= url.port <= 65535:
            valid = False
        if not valid:
            raise ValueError
    except (ValueError, TypeError):
        raise LifecycleError("ADSCRAWL_BASE_URL must be a trusted HTTPS origin (HTTP only on loopback).") from None
    return value.rstrip("/")


def proxy_from_env():
    server = os.environ.get("ADSCRAWL_PROXY_SERVER", "")
    username = os.environ.get("ADSCRAWL_PROXY_USERNAME")
    password = os.environ.get("ADSCRAWL_PROXY_PASSWORD")
    try:
        url = urllib.parse.urlsplit(server)
        if (url.scheme not in {"http", "socks5"} or not url.hostname
                or not url.port or url.username is not None or url.password is not None
                or url.path not in ("", "/") or url.query or url.fragment
                or any(c.isspace() for c in server)):
            raise ValueError
    except ValueError:
        raise LifecycleError("Set ADSCRAWL_PROXY_SERVER to http://host:port or socks5://host:port.") from None
    proxy = {"server": server}
    if username is not None or password is not None:
        if not username or not password or not username.strip() or not password.strip():
            raise LifecycleError("Provide both proxy username and password, or omit both.")
        proxy.update(username=username, password=password)
    return proxy


def runtime_summary(data):
    runtime = data.get("runtime")
    if not isinstance(runtime, dict) or runtime.get("status") not in STATES:
        raise LifecycleError("Invalid runtime response; query the profile to confirm state.")
    result = {"status": runtime["status"]}
    if runtime.get("runtimeKind") in {"neko", "worker_cdp"}:
        result["runtimeKind"] = runtime["runtimeKind"]
    return result


def profile_summary(data):
    return {"id": identifier(data.get("id")), "runtime": runtime_summary(data)}


def identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", value):
        raise LifecycleError("Invalid profile ID.")
    return value


class Client:
    def __init__(self, timeout):
        self.base = origin(os.environ.get("ADSCRAWL_BASE_URL", "https://api.adscrawl.net"))
        self.key = os.environ.get("ADSCRAWL_API_KEY", "")
        if not self.key or any(c.isspace() for c in self.key):
            raise LifecycleError("ADSCRAWL_API_KEY is required and must not contain whitespace.")
        self.timeout = timeout
        self.opener = urllib.request.build_opener(NoRedirect())

    def request(self, method, path, body=None, expected=(200,)):
        headers = {"X-API-Key": self.key, "Accept": "application/json"}
        payload = None
        if body is not None:
            headers["Content-Type"] = "application/json"
            payload = json.dumps(body).encode("utf-8")
        request = urllib.request.Request(self.base + path, data=payload, headers=headers, method=method)
        try:
            with self.opener.open(request, timeout=self.timeout) as response:
                status = response.status
                data = json.load(response)
        except urllib.error.HTTPError as error:
            with error:
                try:
                    code = json.loads(error.read(65536)).get("code")
                except (ValueError, AttributeError, OSError, http.client.HTTPException):
                    code = None
            safe_code = code if isinstance(code, str) and code in SAFE_CODES else "API_ERROR"
            raise LifecycleError(f"HTTP {error.code} {safe_code}; query profile state before retrying mutations.") from None
        except (OSError, ValueError, urllib.error.URLError, http.client.HTTPException):
            raise LifecycleError("Request failed or response was invalid; query profile state before retrying mutations.") from None
        if status not in expected or not isinstance(data, dict):
            raise LifecycleError("Unexpected API response; query profile state to confirm the outcome.")
        return status, data

    def stop(self, path, attempts, interval):
        status, data = self.request("POST", path + "/stop", expected=(200, 202))
        runtime = runtime_summary(data)
        if data.get("ok") is not True or runtime["status"] != ("stopped" if status == 200 else "stopping"):
            raise LifecycleError("Unexpected stop response; shutdown is unconfirmed.")
        if status == 200:
            return {"ok": True, "runtime": runtime}
        for attempt in range(attempts):
            if attempt:
                time.sleep(interval)
            _, data = self.request("GET", path)
            runtime = runtime_summary(data)
            if runtime["status"] == "stopped":
                return {"ok": True, "runtime": runtime}
        raise LifecycleError("Stop polling limit reached; shutdown is unconfirmed. Query status and retry stop on this profile.")


def positive_int(value):
    try:
        result = int(value)
        if result > 0:
            return result
    except ValueError:
        pass
    raise argparse.ArgumentTypeError("must be a positive integer")


def positive_float(value):
    try:
        result = float(value)
        if math.isfinite(result) and result > 0:
            return result
    except ValueError:
        pass
    raise argparse.ArgumentTypeError("must be a finite positive number")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--request-timeout", type=positive_float, default=60)
    commands = parser.add_subparsers(dest="command", required=True)
    create = commands.add_parser("create", help="Save a configuration; does not start it")
    create.add_argument("--remark", default="API lifecycle example")
    listing = commands.add_parser("list", help="List safe profile states and user quota")
    listing.add_argument("--page", type=positive_int, default=1)
    for name in ("get", "start", "stop"):
        command = commands.add_parser(name)
        command.add_argument("id")
        if name == "stop":
            command.add_argument("--poll-attempts", type=positive_int, default=30)
            command.add_argument("--poll-interval", type=positive_float, default=2)
    args = parser.parse_args()
    try:
        client = Client(args.request_timeout)
        path = "/cloud-browsers"
        if hasattr(args, "id"):
            path += "/" + identifier(args.id)
        if args.command == "create":
            if len(args.remark.strip()) > 255:
                raise LifecycleError("Remark must be at most 255 characters after trimming.")
            _, data = client.request("POST", path, {"remark": args.remark,
                "browserSettings": {"viewport": {"width": 1440, "height": 900}}}, expected=(201,))
            if data.get("ok") is not True:
                raise LifecycleError("Create was not confirmed; inspect the profile list before repeating.")
            result = {"ok": True, "id": identifier(data.get("id"))}
        elif args.command == "list":
            _, data = client.request("GET", path + f"?page={args.page}&pageSize=10")
            if data.get("ok") is not True or not isinstance(data.get("data"), list):
                raise LifecycleError("Invalid list response.")
            result = {"ok": True, "data": [profile_summary(item) for item in data["data"]]}
            for field in ("limit", "runningLimit", "runningCount"):
                if type(data.get(field)) is not int or (field != "limit" and data[field] < 0):
                    raise LifecycleError("Invalid quota response.")
                result[field] = data[field]
        elif args.command == "get":
            _, data = client.request("GET", path)
            result = profile_summary(data)
        elif args.command == "start":
            _, data = client.request("POST", path + "/start", {"proxy": proxy_from_env()})
            runtime = runtime_summary(data)
            if data.get("ok") is not True or runtime["status"] != "running":
                raise LifecycleError("Start was not confirmed running; query the profile before retrying.")
            result = {"ok": True, "runtime": runtime}
        else:
            result = client.stop(path, args.poll_attempts, args.poll_interval)
        print(json.dumps(result))
        return 0
    except LifecycleError as error:
        # Only our locally constructed errors are safe to display.
        print(str(error), file=sys.stderr)
        return 1
    except (AttributeError, TypeError, KeyError):
        print("Invalid API response; query profile state.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
