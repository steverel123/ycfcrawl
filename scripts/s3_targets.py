"""Shared S3 clients for RunPod volume and Cloudflare R2."""
from __future__ import annotations

import os

import boto3
from botocore.config import Config

PREFIX = os.environ.get("RUNPOD_S3_PREFIX", "clean-yachts")


def make_client(target: str):
    if target == "volume":
        return boto3.client(
            "s3",
            endpoint_url=os.environ.get("RUNPOD_S3_ENDPOINT", "https://s3api-us-ca-2.runpod.io"),
            region_name=os.environ.get("RUNPOD_S3_REGION", "us-ca-2"),
            aws_access_key_id=os.environ["RUNPOD_S3_ACCESS_KEY_ID"],
            aws_secret_access_key=os.environ["RUNPOD_S3_SECRET_ACCESS_KEY"],
            config=Config(max_pool_connections=32),
        ), os.environ.get("RUNPOD_S3_BUCKET", "jc7mhii8r8")
    if target == "r2":
        return boto3.client(
            "s3",
            endpoint_url=f"https://{os.environ['R2_ACCOUNT_ID']}.r2.cloudflarestorage.com",
            region_name="auto",
            aws_access_key_id=os.environ["R2_ACCESS_KEY_ID"],
            aws_secret_access_key=os.environ["R2_SECRET_ACCESS_KEY"],
            config=Config(max_pool_connections=32),
        ), os.environ["R2_BUCKET"]
    raise ValueError(target)


def key_to_processing_rel(key: str) -> str | None:
    """clean-yachts/slug/001.jpg -> slug/images/001.jpg"""
    if not key.startswith(f"{PREFIX}/"):
        return None
    rest = key[len(PREFIX) + 1 :]
    parts = rest.split("/")
    if len(parts) != 2 or not parts[1].lower().endswith((".jpg", ".jpeg")):
        return None
    return f"{parts[0]}/images/{parts[1]}"


def processing_rel_to_key(rel: str) -> str:
    """slug/images/001.jpg -> clean-yachts/slug/001.jpg"""
    parts = [p for p in rel.replace("\\", "/").split("/") if p]
    if len(parts) == 3 and parts[1] == "images":
        return f"{PREFIX}/{parts[0]}/{parts[2]}"
    if len(parts) == 2:
        return f"{PREFIX}/{parts[0]}/{parts[1]}"
    raise ValueError(f"unexpected rel: {rel}")
