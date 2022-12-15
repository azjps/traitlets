"""
Tests for argcomplete handling by traitlets.config.application.Application
"""

import os
import sys
import typing as t
from tempfile import TemporaryFile

import pytest

argcomplete = pytest.importorskip("argcomplete")

from traitlets import Unicode
from traitlets.config.application import Application
from traitlets.config.configurable import Configurable
from traitlets.config.loader import KVArgParseConfigLoader


class ArgcompleteApp(Application):
    """Override loader to pass through kwargs for argcomplete testing"""

    argcomplete_kwargs: t.Dict[str, t.Any]

    def _create_loader(self, argv, aliases, flags, classes):
        loader = KVArgParseConfigLoader(argv, aliases, flags, classes=classes, log=self.log)
        loader._argcomplete_kwargs = self.argcomplete_kwargs  # type: ignore[attr-defined]
        return loader


class SubApp1(ArgcompleteApp):
    pass


class SubApp2(ArgcompleteApp):
    @classmethod
    def get_subapp_instance(cls, app: Application) -> Application:
        app.clear_instance()  # since Application is singleton, need to clear main app
        return cls.instance(parent=app)  # type: ignore[no-any-return]


class MainApp(ArgcompleteApp):
    subcommands = {
        "subapp1": (SubApp1, "First subapp"),
        "subapp2": (SubApp2.get_subapp_instance, "Second subapp"),
    }


class TestArgcomplete:
    IFS = "\013"
    COMP_WORDBREAKS = " \t\n\"'><=;|&(:"

    @pytest.fixture
    def argcomplete_on(self):
        """Mostly borrowed from argcomplete's unit test fixtures

        Set up environment variables to mimic those passed by argcomplete
        """
        _old_environ = os.environ
        os.environ = os.environ.copy()  # type: ignore[assignment]
        os.environ["_ARGCOMPLETE"] = "1"
        os.environ["_ARC_DEBUG"] = "yes"
        os.environ["IFS"] = self.IFS
        os.environ["_ARGCOMPLETE_COMP_WORDBREAKS"] = self.COMP_WORDBREAKS
        os.environ["_ARGCOMPLETE"] = "1"
        yield
        os.environ = _old_environ

    def run_completer(
        self,
        app: ArgcompleteApp,
        command: str,
        point: t.Union[str, int, None] = None,
        **kwargs: t.Any,
    ) -> t.List[str]:
        """Mostly borrowed from argcomplete's unit tests

        Modified to take an application instead of an ArgumentParser

        Command is the current command being completed and point is the index
        into the command where the completion is triggered.
        """
        if point is None:
            point = str(len(command))
        try:
            # NOTE: argcomplete does not have a version attribute as far as I can find,
            # when argcomplete was promoted to version 2.0, the change was made to
            # have output_stream be in text mode instead of binary mode
            # https://github.com/kislyuk/argcomplete/commit/efe11f30290fbd298aa5e6505556bb0ab1596ea0
            # We hackily "check" for argcomplete version by seeing if it still contains
            # py2 compatibility helpers.
            argcomplete.ensure_str
            write_mode = "wb+"
        except AttributeError:
            write_mode = "wt+"
        # print("Writing completions to temp file with mode=", write_mode)
        with TemporaryFile(mode=write_mode) as t:
            os.environ["COMP_LINE"] = command
            os.environ["COMP_POINT"] = str(point)
            with pytest.raises(SystemExit) as cm:
                app.argcomplete_kwargs = dict(output_stream=t, exit_method=sys.exit, **kwargs)
                app.initialize()
            if cm.value.code != 0:
                raise Exception(f"Unexpected exit code {cm.value.code}")
            t.seek(0)
            out: str
            if write_mode == "wb+":
                out = t.read().decode()
            else:
                out = t.read()
            return out.split(self.IFS)

    def test_complete_simple_app(self, argcomplete_on):
        app = ArgcompleteApp()
        expected = [
            '--help',
            '--debug',
            '--show-config',
            '--show-config-json',
            '--log-level',
            '--Application.',
            '--ArgcompleteApp.',
        ]
        assert set(self.run_completer(app, "app --")) == set(expected)

        # completing class traits
        assert set(self.run_completer(app, "app --App")) > {
            '--Application.show_config',
            '--Application.log_level',
            '--Application.log_format',
        }

    def test_complete_custom_completers(self, argcomplete_on):
        app = ArgcompleteApp()
        # test pre-defined completers for Bool/Enum
        assert set(self.run_completer(app, "app --Application.log_level=")) > {"DEBUG", "INFO"}
        assert set(self.run_completer(app, "app --ArgcompleteApp.show_config ")) == {
            "0",
            "1",
            "true",
            "false",
        }

        # test custom completer and mid-command completions
        class CustomCls(Configurable):
            val = Unicode().tag(
                config=True, argcompleter=argcomplete.completers.ChoicesCompleter(["foo", "bar"])
            )

        class CustomApp(ArgcompleteApp):
            classes = [CustomCls]
            aliases = {("v", "val"): "CustomCls.val"}

        app = CustomApp()
        assert self.run_completer(app, "app --val ") == ["foo", "bar"]
        assert self.run_completer(app, "app --val=") == ["foo", "bar"]
        assert self.run_completer(app, "app -v ") == ["foo", "bar"]
        assert self.run_completer(app, "app -v=") == ["foo", "bar"]
        assert self.run_completer(app, "app --CustomCls.val  ") == ["foo", "bar"]
        assert self.run_completer(app, "app --CustomCls.val=") == ["foo", "bar"]
        completions = self.run_completer(app, "app --val= abc xyz", point=10)
        # fixed in argcomplete >= 2.0 to return latter below
        assert completions == ["--val=foo", "--val=bar"] or completions == ["foo", "bar"]
        assert self.run_completer(app, "app --val  --log-level=", point=10) == ["foo", "bar"]

    # TODO: don't have easy way of testing subcommands yet, since we want
    # to inject _argcomplete_kwargs to subapp. Could use mocking for this
    # def test_complete_subcommands_subapp1(self, argcomplete_on):
    #     # subcommand handling modifies _ARGCOMPLETE env var global state, so
    #     # only can test one completion per unit test
    #     app = MainApp()
    #     assert set(self.run_completer(app, "app subapp1 --Sub")) > {
    #         '--SubApp1.show_config',
    #         '--SubApp1.log_level',
    #         '--SubApp1.log_format',
    #     }
    #
    # def test_complete_subcommands_subapp2(self, argcomplete_on):
    #     app = MainApp()
    #     assert set(self.run_completer(app, "app subapp2 --")) > {
    #         '--Application.',
    #         '--SubApp2.',
    #     }

    def test_complete_subcommands_main(self, argcomplete_on):
        app = MainApp()
        completions = set(self.run_completer(app, "app --"))
        assert completions > {'--Application.', '--MainApp.'}
        assert "--SubApp1." not in completions and "--SubApp2." not in completions
