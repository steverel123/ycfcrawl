-- YachtCharterFleet data: metadata in Postgres, images in Storage.
-- Run in Supabase SQL Editor after creating a public bucket named "yacht-images".

create extension if not exists "pgcrypto";

create table if not exists public.yachts (
  id text primary key,
  slug text not null,
  folder_name text not null unique,
  name text not null,
  source_url text,
  length text,
  builder text,
  year_built text,
  guests integer,
  cabins integer,
  price jsonb,
  specifications jsonb,
  specs_normalized jsonb,
  amenities jsonb default '[]'::jsonb,
  toys jsonb,
  description text,
  description_sections jsonb,
  meta_description text,
  metadata jsonb not null,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create index if not exists yachts_slug_idx on public.yachts (slug);
create index if not exists yachts_name_idx on public.yachts (name);

create table if not exists public.yacht_images (
  id bigint generated always as identity primary key,
  yacht_id text not null references public.yachts (id) on delete cascade,
  sort_order integer not null,
  file_name text not null,
  storage_path text not null unique,
  public_url text not null,
  source_url text,
  cleaned boolean not null default false,
  created_at timestamptz not null default now(),
  unique (yacht_id, sort_order)
);

create index if not exists yacht_images_yacht_id_idx on public.yacht_images (yacht_id);

-- Optional: public read for anon clients (adjust for your app).
alter table public.yachts enable row level security;
alter table public.yacht_images enable row level security;

create policy "Public read yachts"
  on public.yachts for select
  using (true);

create policy "Public read yacht_images"
  on public.yacht_images for select
  using (true);

-- Storage bucket (Dashboard: Storage → New bucket → name "yacht-images" → Public)
-- Then add policy: allow public SELECT on bucket yacht-images.
