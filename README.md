# The Black Friday Edit

Ella's shopping wishlist, published with GitHub Pages.

- **Add a piece:** use "+ Add a piece" on the site. It opens a prefilled GitHub issue; press Create and the `Update wishlist from issue` workflow adds it to `data/items.json` (with the shop's photo, or one dragged into the issue) and republishes the site.
- **Edit / remove:** the Edit button on each card works the same way.
- **Price checks:** scheduled Claude runs on 25, 27 and 30 November 2026 update `status`, `salePrice`, `scanNote` and `checkedAt` on each item, set `lastRun`, and push.

Only issues opened by the repo owner change the list.
