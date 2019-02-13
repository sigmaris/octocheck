# octocheck

This script parses the output of some linters, build and testing tools,
and reports them as Checks to Github (https://github.blog/2018-05-07-introducing-checks-api/).
This lets you annotate pull requests with richer results than pass/fail status.
This script is useful for any type of CI service which doesn't have a built in
Github App integration, for example Jenkins, Buildbot or any custom CI system.
