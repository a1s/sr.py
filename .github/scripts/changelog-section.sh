#!/usr/bin/env bash
#
# Print the CHANGELOG.md section for one version.
#
# Usage: changelog-section.sh VERSION [FILE]
#
# Copies the lines between the `## [VERSION]` heading and the next `## `
# heading, dropping the heading itself and the blank lines at either end.
# Exits non-zero when the version has no section, so a release cannot go
# out with empty notes.

set -euo pipefail

version=${1:?usage: changelog-section.sh VERSION [FILE]}
file=${2:-CHANGELOG.md}

section=$(awk -v version="$version" '
	BEGIN { heading = "## [" version "]" }

	# The wanted heading, either bare or followed by a date.
	index($0, heading) == 1 &&
	(length($0) == length(heading) || substr($0, length(heading) + 1, 1) == " ") {
		copying = 1
		next
	}

	# The next release heading ends the section.
	copying && /^## / { exit }

	# Hold blank lines back until a later line proves they are interior
	# ones, which trims the leading and trailing padding in one pass.
	copying {
		if ($0 ~ /^[[:space:]]*$/) { pending++; next }
		for (; printed && pending > 0; pending--) print ""
		pending = 0
		print
		printed = 1
	}
' "$file")

if [ -z "$section" ]; then
	printf 'changelog-section: no entry for version %s in %s\n' "$version" "$file" >&2
	exit 1
fi

printf '%s\n' "$section"
