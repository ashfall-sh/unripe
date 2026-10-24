# Running unripe in CI

The [GitHub Action](../README.md#in-github-actions) and the
[GitLab template](../README.md#in-gitlab-ci) are the easy paths. Everywhere
else, unripe is a Python command with no dependencies, so any job with
Python 3.11 or newer can run it.

Three things are the same on every system:

- Pin the version you install. A tool that checks your supply chain should
  not float to whatever was published this morning.
- On a pull request, pass `--since` with the target branch so only the
  versions the change adds are checked. CI systems clone shallowly, so fetch
  that branch first.
- `--report junit:FILE` writes a JUnit report most CI systems can show next
  to the run, without changing what goes to the log.

Exit codes: 0 everything is old enough, 1 something is too new or unknown,
2 a lockfile could not be read or an option is wrong.

## Azure Pipelines

```yaml
pool:
  vmImage: ubuntu-latest

steps:
  - task: UsePythonVersion@0
    inputs:
      versionSpec: "3.12"
  - script: python -m pip install --quiet unripe==0.7.0
    displayName: Install unripe
  - script: |
      since=""
      if [ "$BUILD_REASON" = "PullRequest" ]; then
        target="${SYSTEM_PULLREQUEST_TARGETBRANCH#refs/heads/}"
        git fetch --quiet --depth=1 origin "$target"
        since="--since origin/$target"
      fi
      unripe check $since --report junit:unripe.xml
    displayName: unripe
  - task: PublishTestResults@2
    condition: succeededOrFailed()
    inputs:
      testResultsFormat: JUnit
      testResultsFiles: unripe.xml
```

Azure Pipelines passes its variables to scripts as environment variables, so
`Build.Reason` is `$BUILD_REASON` there.

## Jenkins

A declarative pipeline in a Python container:

```groovy
pipeline {
  agent { docker { image 'python:3.12' } }
  stages {
    stage('unripe') {
      steps {
        sh 'pip install --quiet --user unripe==0.7.0'
        sh '''
          since=""
          if [ -n "$CHANGE_TARGET" ]; then
            git fetch --quiet --depth=1 origin "$CHANGE_TARGET"
            since="--since origin/$CHANGE_TARGET"
          fi
          python -m unripe check $since --report junit:unripe.xml
        '''
      }
    }
  }
  post {
    always {
      junit allowEmptyResults: true, testResults: 'unripe.xml'
    }
  }
}
```

`CHANGE_TARGET` is set by multibranch pipelines on pull request builds.

## CircleCI

```yaml
version: 2.1

jobs:
  unripe:
    docker:
      - image: cimg/python:3.12
    steps:
      - checkout
      - run: pip install --quiet unripe==0.7.0
      - run:
          name: unripe
          command: |
            mkdir -p results
            unripe check --report junit:results/unripe.xml
      - store_test_results:
          path: results

workflows:
  check:
    jobs:
      - unripe
```

## Bitbucket Pipelines

```yaml
pipelines:
  pull-requests:
    "**":
      - step:
          name: unripe
          image: python:3.12
          script:
            - pip install --quiet unripe==0.7.0
            - git fetch --quiet --depth=1 origin "$BITBUCKET_PR_DESTINATION_BRANCH"
            - unripe check --since "origin/$BITBUCKET_PR_DESTINATION_BRANCH" --report junit:test-results/unripe.xml
  default:
    - step:
        name: unripe
        image: python:3.12
        script:
          - pip install --quiet unripe==0.7.0
          - unripe check --report junit:test-results/unripe.xml
```

Bitbucket picks up JUnit files under `test-results/` by itself.

## Anything else

```bash
python -m pip install unripe==0.7.0
unripe check --since origin/main
```

or without installing anything into the job's Python:

```bash
uvx unripe@0.7.0 check --since origin/main
```

If the job sits behind a proxy that inspects TLS, point `UNRIPE_CA_BUNDLE`
or `SSL_CERT_FILE` at its CA bundle. `unripe doctor` shows what unripe can
see from inside the job: config, lockfiles, proxy settings, and whether each
registry answers.

## Trying it without failing builds

Add `--exit-zero` (or `fail: false` in the Action). Everything is still
reported; the job just doesn't fail. Take it out once the report is quiet.
