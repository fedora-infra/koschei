#!/usr/bin/make -f

spec := koschei.spec
outdir := $(CURDIR)
tmpdir := build
gitdir := $(dir $(spec))/.git

rev := $(shell ref="$$(cat $(gitdir)/HEAD)"; [[ "$$ref" == "ref: "* ]] && ref="$$(cat "$(gitdir)/$${ref\#ref: }")"; echo "$${ref:0:7}")
date := $(shell date +%Y%m%d.%H%M)

version := $(shell sed -n '/Version:/{s/.* //;p}' $(spec))
release := $(date).git.$(rev)

srpm: $(outdir)/koschei-$(version)-$(release).src.rpm

$(tmpdir)/koschei.spec: $(spec)
	@mkdir -p $(tmpdir)
	sed '/^Release:/s/\(: *\).*/\1$(release)%{?dist}/' $< >$@

$(tmpdir)/$(version).tar.gz: $(gitdir)/..
	@mkdir -p $(tmpdir)
	tar c --exclude-vcs --exclude-vcs-ignores -C $< --transform 's|^\.|koschei-$(version)|' . | gzip -9 >$@

$(outdir)/koschei-$(version)-$(release).src.rpm: $(tmpdir)/koschei.spec $(tmpdir)/$(version).tar.gz
	@mkdir -p $(outdir)
	rpmbuild -D'_srcrpmdir $(outdir)' -D'_sourcedir $(tmpdir)' -bs $(tmpdir)/koschei.spec

.PHONY: srpm
