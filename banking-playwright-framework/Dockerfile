# Reproducible test runner image. The base tag MUST equal <playwright.version> in pom.xml:
# it contains JDK, Maven and the exact browser builds that Playwright 1.56.0 was released with.
FROM mcr.microsoft.com/playwright/java:v1.56.0-noble

ENV PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1 \
    MAVEN_OPTS="-Xmx2g"

WORKDIR /tests

# Dependency layer first -> cached until pom.xml changes.
COPY pom.xml .
RUN mvn -B -ntp dependency:go-offline

COPY src ./src
RUN mvn -B -ntp -o test-compile

# docker build -t nb-e2e . && docker run --rm --ipc=host nb-e2e                  (full suite)
# docker run --rm --ipc=host nb-e2e mvn -B -o test -Pci -Dgroups=smoke            (smoke only)
CMD ["mvn", "-B", "-o", "test", "-Pci", "-Denv=ci"]
