-- After creating public bucket "yacht-images" in Storage UI, run:

create policy "Public read yacht images"
  on storage.objects for select
  using (bucket_id = 'yacht-images');

-- Service role (upload script) bypasses RLS. For client-side uploads,
-- add authenticated INSERT policies as needed.
