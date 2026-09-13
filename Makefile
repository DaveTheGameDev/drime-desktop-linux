SPEC    := drime-desktop.spec
NAME    := drime-desktop
VERSION := $(shell sed -n 's/^Version:[[:space:]]*//p' $(SPEC) | head -1)
TOPDIR  := $(CURDIR)/build
DEBDIR  := $(TOPDIR)/deb
DEB     := $(DEBDIR)/$(NAME)_$(VERSION)_all.deb
FLATPAK_ID       := io.github.davethegamedev.DrimeDesktop
FLATPAK_MANIFEST := flatpak/$(FLATPAK_ID).yml
FLATPAK_DIR      := $(TOPDIR)/flatpak
FLATPAK          := $(FLATPAK_DIR)/$(NAME)-$(VERSION).flatpak
FLATHUB_REPO     := https://dl.flathub.org/repo/flathub.flatpakrepo
# e.g. make rpm RPMBUILD_OPTS=--nodeps when python3-devel is not installed
RPMBUILD_OPTS ?=

.PHONY: version test tarball srpm rpm lint install-local deb deb-lint install-local-deb \
        flatpak install-local-flatpak release clean

version:
	@echo $(VERSION)

# Needs python3-pytest
test:
	python3 -m pytest -q tests

tarball:
	mkdir -p $(TOPDIR)/SOURCES
	git ls-files -co --exclude-standard | tar -czf $(TOPDIR)/SOURCES/$(NAME)-$(VERSION).tar.gz \
	    --transform 's,^,$(NAME)-$(VERSION)/,' --no-recursion -T -

# --- RPM (Fedora) ------------------------------------------------------------

srpm: tarball
	rpmbuild -bs $(RPMBUILD_OPTS) --define "_topdir $(TOPDIR)" $(SPEC)

rpm: tarball
	rpmbuild -ba $(RPMBUILD_OPTS) --define "_topdir $(TOPDIR)" $(SPEC)

lint:
	rpmlint -r $(NAME).rpmlintrc $(SPEC) $(TOPDIR)/RPMS/noarch/*.rpm || true
	rpm -qpl $(TOPDIR)/RPMS/noarch/*.rpm

install-local:
	sudo dnf install -y $(TOPDIR)/RPMS/noarch/$(NAME)-$(VERSION)-*.noarch.rpm

# --- DEB (Ubuntu/Debian) -----------------------------------------------------
# Needs: debhelper dh-python lintian desktop-file-utils appstream

debian/changelog: $(SPEC) scripts/deb-changelog.sh
	scripts/deb-changelog.sh > $@

deb: debian/changelog
	mkdir -p $(DEBDIR)
	dpkg-buildpackage -us -uc -b
	mv ../$(NAME)_$(VERSION)_all.deb ../$(NAME)_$(VERSION)_*.buildinfo ../$(NAME)_$(VERSION)_*.changes $(DEBDIR)/

deb-lint:
	lintian --fail-on error,warning -i $(DEB)
	dpkg -c $(DEB)

install-local-deb:
	sudo apt install -y $(DEB)

# --- Flatpak (any distribution) ----------------------------------------------
# Needs: flatpak flatpak-builder, the flathub remote (flatpak remote-add --user --if-not-exists
# flathub $(FLATHUB_REPO)); the GNOME 50 SDK is pulled in by --install-deps-from.

flatpak:
	flatpak-builder --force-clean --user --install-deps-from=flathub --ccache \
	    --state-dir=$(FLATPAK_DIR)/.flatpak-builder --repo=$(FLATPAK_DIR)/repo \
	    $(FLATPAK_DIR)/build $(FLATPAK_MANIFEST)
	flatpak build-bundle --runtime-repo=$(FLATHUB_REPO) $(FLATPAK_DIR)/repo $(FLATPAK) $(FLATPAK_ID)

install-local-flatpak:
	flatpak install --user -y $(FLATPAK)

# --- Release -----------------------------------------------------------------
# Manual release (when not using the GitHub Actions workflow): builds the RPM and
# publishes it, plus build/deb/*.deb and build/flatpak/*.flatpak if `make deb` /
# `make flatpak` were run (or the CI artifacts were downloaded there), with the
# standard "which file do I download?" header. The tag v$(VERSION) must already be pushed.
release: rpm
	scripts/release-notes.sh $(VERSION) $(notdir $(wildcard build/RPMS/noarch/*.rpm)) > build/release-notes.md
	gh release create v$(VERSION) build/RPMS/noarch/*.rpm build/SRPMS/*.src.rpm build/SOURCES/*.tar.gz \
	    $(wildcard $(DEBDIR)/*.deb) $(wildcard $(FLATPAK_DIR)/*.flatpak) \
	    --title v$(VERSION) --notes-file build/release-notes.md

clean:
	rm -rf $(TOPDIR) .flatpak-builder debian/changelog debian/files debian/drime-desktop debian/.debhelper \
	    debian/*.substvars debian/*.debhelper debian/*.debhelper.log debian/debhelper-build-stamp
