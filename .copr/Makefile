#!/usr/bin/make -f

spec := koschei.spec
outdir := $(CURDIR)
tmpdir := build
gitdir := $(dir $(spec))/.git

GIT := git --git-dir $(gitdir) --work-tree $(gitdir)/..

rev := $(shell $(GIT) rev-parse --short HEAD)
date := $(shell date +%Y%m%d)

version := $(shell sed -n '/Version:/{s/.* //;p}' $(spec))
release := $(date).git.$(rev)

srpm: $(outdir)/koschei-$(version)-$(release).src.rpm

$(tmpdir)/koschei.spec: $(spec)
	@mkdir -p $(tmpdir)
	sed '/^Release:/s/\(: *\).*/\1$(release)%{?dist}/' $< >$@

$(tmpdir)/$(version).tar.gz: $(gitdir)
	@mkdir -p $(tmpdir)
	$(GIT) archive --prefix koschei-$(version)/ $(rev) | gzip -9 >$@

$(outdir)/koschei-$(version)-$(release).src.rpm: $(tmpdir)/koschei.spec $(tmpdir)/$(version).tar.gz
	@mkdir -p $(outdir)
	rpmbuild -D'_srcrpmdir $(outdir)' -D'_sourcedir $(tmpdir)' -bs $(tmpdir)/koschei.spec

.PHONY: srpm
