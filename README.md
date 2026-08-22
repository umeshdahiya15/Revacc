This is a [Next.js](https://nextjs.org) project bootstrapped with [`create-next-app`](https://nextjs.org/docs/app/api-reference/cli/create-next-app).

## Getting Started

First, run the development server:

```bash
npm run dev
# or
yarn dev
# or
pnpm dev
# or
bun dev
```

Open [http://localhost:3000](http://localhost:3000) with your browser to see the result.

You can start editing the page by modifying `app/page.tsx`. The page auto-updates as you edit the file.

This project uses [`next/font`](https://nextjs.org/docs/app/building-your-application/optimizing/fonts) to automatically optimize and load [Geist](https://vercel.com/font), a new font family for Vercel.

## Learn More

To learn more about Next.js, take a look at the following resources:

- [Next.js Documentation](https://nextjs.org/docs) - learn about Next.js features and API.
- [Learn Next.js](https://nextjs.org/learn) - an interactive Next.js tutorial.

You can check out [the Next.js GitHub repository](https://github.com/vercel/next.js) - your feedback and contributions are welcome!

## Deploy on Vercel

The easiest way to deploy your Next.js app is to use the [Vercel Platform](https://vercel.com/new?utm_medium=default-template&filter=next.js&utm_source=create-next-app&utm_campaign=create-next-app-readme) from the creators of Next.js.

Check out our [Next.js deployment documentation](https://nextjs.org/docs/app/building-your-application/deploying) for more details.

## Scientific provenance and unavailable tools

Pipeline outputs distinguish live, cached-real, local-analysis, user-provided, and unavailable results. IEDB CTL/HTL epitopes require a live or cached-real IEDB response; exhausted retries pause the run without synthetic epitope rows. ABCpred, VaxiJen, and AlgPred local analyses are labeled as such when their external services are unavailable. PyDock and GROMACS molecular-dynamics outputs are not implemented/configured and are reported as unavailable in exports until real tools and validated inputs/outputs are supplied.

## Deploy the backend on Railway

The repository-root `Dockerfile` is the Railway backend image. It installs the dependencies into the image interpreter, starts production Uvicorn without `--reload`, binds to `0.0.0.0`, and uses Railway's runtime `PORT` (falling back to 8000 only for local `docker run`). See [`backend/README.md`](backend/README.md#railway-deployment) for the exact Railway and Vercel handoff steps.

For the confirmed deployment, set `MEV_CORS_ORIGINS=https://revacc.vercel.app` in Railway and `NEXT_PUBLIC_API_URL=https://revacc-production.up.railway.app` in Vercel. The `https://` scheme is required for deployed URLs; do not use a bare hostname.

## Publish the backend image to GHCR

[`publish-backend-image.yml`](.github/workflows/publish-backend-image.yml) builds the repository-root `Dockerfile` and publishes it to GitHub Container Registry on pushes to `main` and version tags beginning with `v`. The resulting image URL pattern is:

```text
ghcr.io/<owner>/<repository>
```

The workflow publishes `main` and `latest` for the default branch, a `sha-<short-commit>` tag for each published commit, and semver tags for version-tag pushes. The owner and repository are normalized to lowercase for GHCR. No real image URL exists until GitHub Actions has completed a run; see [`backend/README.md`](backend/README.md#publish-and-deploy-the-ghcr-image) for GHCR visibility and Railway options.
