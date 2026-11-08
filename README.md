# crawler2RSS

Python scrapers write articles to PostgreSQL and generate RSS feeds. The NAS runs this project as a scheduled, one-shot Docker Compose job.

The Playwright Python package and base image must use the same version. HobbySearch uses Firefox.

## NAS deployment

Deploy the project to `/volume1/docker/crawler2rss`, keeping `runtime.env` on the NAS only. It contains `DATABASE_URL` and is loaded by python-dotenv. Compose overrides `RSS_OUTPUT_DIR` to `/rss`, mounted to the existing WordPress RSS directory. The external `nas_network` must exist and provide the configured PostgreSQL hostname. Logs are stored in `logs/`.

Build with `docker compose build crawler`. DSM runs `run-scheduled.sh` at 08:00 and 20:00; its lock prevents overlapping runs. Each run creates a container and removes it after the job finishes. Running the job writes to the configured database and RSS directory. The application currently catches some individual scraper errors, so a zero exit code does not guarantee every source succeeded; inspect logs as well.

The repository is maintained in `/home/gui/projects/crawler2rss`; pushing to GitHub does not update the NAS. To deploy a change, copy the intended source files to the NAS deployment directory without overwriting `runtime.env`, rebuild the image, and verify the job before relying on the next schedule.

Do not commit `runtime.env`, RSS output, or logs. Keep the old deployment until the new job and schedule have been verified.
