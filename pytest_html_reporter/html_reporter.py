import glob
import json
import logging
import os
import time
import shutil
from datetime import date, datetime
from os.path import isfile, join

import pytest

from html_page.archive_body import ArchiveBody
from html_page.archive_row import ArchiveRow
from html_page.floating_error import FloatingError
from html_page.screenshot_details import ScreenshotDetails
from html_page.suite_row import SuiteRow
from html_page.template import HtmlTemplate
from html_page.test_row import TestRow
from pytest_html_reporter.util import suite_highlights, generate_suite_highlights, max_rerun
from pytest_html_reporter.time_converter import time_converter
from pytest_html_reporter.const_vars import ConfigVars


class HTMLReporter(object):
    def __init__(self, path, archive_count, config):
        self.json_data = {'content': {'suites': {0: {'status': {}, 'tests': {0: {}}, }, }}}
        self.path = path
        self.archive_count = archive_count
        self.config = config
        has_rerun = config.pluginmanager.hasplugin("rerunfailures")
        self.rerun = 0 if has_rerun else None

    def pytest_runtest_teardown(self, item, nextitem):
        ConfigVars._test_name = item.name

        _test_end_time = time.time()
        ConfigVars._duration = _test_end_time - ConfigVars._start_execution_time

        if (self.rerun is not None) and (max_rerun() is not None): self.previous_test_name(ConfigVars._test_name)
        self._test_names(ConfigVars._test_name)
        self.append_test_metrics_row()

    def previous_test_name(self, _test_name):
        if ConfigVars._previous_test_name == _test_name:
            self.rerun += 1
        else:
            ConfigVars._scenario.append(_test_name)
            self.rerun = 0
            ConfigVars._previous_test_name = _test_name

    def pytest_runtest_setup(item):
        ConfigVars._start_execution_time = time.time()

    def pytest_sessionfinish(self, session):
        if ConfigVars._suite_name is not None: self.append_suite_metrics_row(ConfigVars._suite_name)

    def archive_data(self, base, filename):
        path = os.path.join(base, filename)

        if os.path.isfile(path) is True:
            os.makedirs(base + '/archive', exist_ok=True)
            f = 'output.json'

            if isfile(join(base, f)):
                fname = os.path.splitext(f)
                os.rename(base + '/' + f, os.path.join(base + '/archive', fname[0] + '_' +
                                                       str(ConfigVars._start_execution_time) + fname[1]))

    @property
    def report_path(self):
        if '.html' in self.path:
            path = '.' if '.html' in self.path.rsplit('/', 1)[0] else self.path.rsplit('/', 1)[0]
            if path == '': path = '.'
            logfile = os.path.expanduser(os.path.expandvars(path))
            HTMLReporter.base_path = os.path.abspath(logfile)
            return os.path.abspath(logfile), self.path.split('/')[-1]
        else:
            logfile = os.path.expanduser(os.path.expandvars(self.path))
            HTMLReporter.base_path = os.path.abspath(logfile)
            return os.path.abspath(logfile), 'pytest_html_report.html'

    def remove_old_archives(self):
        archive_dir = os.path.abspath(os.path.expanduser(os.path.expandvars(self.path))) + '/archive'

        if self.archive_count != '':
            if int(self.archive_count) == 0:
                if os.path.isdir(archive_dir):
                    shutil.rmtree(archive_dir)
                return

            archive_count = int(self.archive_count) - 1
            if os.path.isdir(archive_dir):
                archives = os.listdir(archive_dir)
                archives.sort(key=lambda f: os.path.getmtime(os.path.join(archive_dir, f)))
                for i in range(0, len(archives) - archive_count):
                    os.remove(os.path.join(archive_dir, archives[i]))

    @pytest.hookimpl(hookwrapper=True)
    def pytest_terminal_summary(self, terminalreporter, exitstatus, config):

        yield
        _execution_time = time.time() - terminalreporter._sessionstarttime

        if ConfigVars._execution_time < 60:
            ConfigVars._execution_time = str(round(_execution_time, 2)) + " secs"
        else:
            _execution_time = str(time.strftime("%H:%M:%S", time.gmtime(round(_execution_time)))) + " Hrs"
        ConfigVars._total = ConfigVars._pass + ConfigVars._fail + ConfigVars._xpass + ConfigVars._xfail + ConfigVars._skip + ConfigVars._error

        if ConfigVars._suite_name is not None:
            base = self.report_path[0]
            path = os.path.join(base, self.report_path[1])

            os.makedirs(base, exist_ok=True)
            self.archive_data(base, self.report_path[1])

            # generate json file
            self.generate_json_data(base)

            # generate trends
            self.update_trends(base)

            # generate archive template
            self.remove_old_archives()
            self.update_archives_template(base) if self.archive_count != '0' else None

            # generate suite highlights
            generate_suite_highlights()

            # generate html report
            live_logs_file = open(path, 'w')
            message = self.renew_template_text('https://i.imgur.com/LRSRHJO.png')
            live_logs_file.write(message)
            live_logs_file.close()

    @pytest.hookimpl(tryfirst=True, hookwrapper=True)
    def pytest_runtest_makereport(self, item, call):

        outcome = yield
        rep = outcome.get_result()
        ConfigVars._suite_name = rep.nodeid.split("::")[0]

        if ConfigVars._initial_trigger:
            self.update_previous_suite_name()
            self.set_initial_trigger()

        if str(ConfigVars._previous_suite_name) != str(ConfigVars._suite_name):
            self.append_suite_metrics_row(ConfigVars._previous_suite_name)
            self.update_previous_suite_name()
        else:
            self.update_counts(rep)

        if rep.when == "call" and rep.passed:
            if hasattr(rep, "wasxfail"):
                self.increment_xpass()
                self.update_test_status("xPASS")
                self.update_test_error("")
            else:
                self.increment_pass()
                self.update_test_status("PASS")
                self.update_test_error("")

        if rep.failed:
            if getattr(rep, "when", None) == "call":
                if hasattr(rep, "wasxfail"):
                    self.increment_xpass()
                    self.update_test_status("xPASS")
                    self.update_test_error("")
                else:
                    self.increment_fail()
                    self.update_test_status("FAIL")
                    if rep.longrepr:
                        longerr = ""
                        for line in rep.longreprtext.splitlines():
                            exception = line.startswith("E   ")
                            if exception:
                                longerr += line + "\n"
                        self.update_test_error(longerr.replace("E    ", ""))
            else:
                self.increment_error()
                self.update_test_status("ERROR")
                if rep.longrepr:
                    longerr = ""
                    for line in rep.longreprtext.splitlines():
                        longerr += line + "\n"
                    self.update_test_error(longerr)

        if rep.skipped:
            if hasattr(rep, "wasxfail"):
                self.increment_xfail()
                self.update_test_status("xFAIL")
                if rep.longrepr:
                    longerr = ""
                    for line in rep.longreprtext.splitlines():
                        exception = line.startswith("E   ")
                        if exception:
                            longerr += line + "\n"
                    self.update_test_error(longerr.replace("E    ", ""))
            else:
                self.increment_skip()
                self.update_test_status("SKIP")
                if rep.longrepr:
                    longerr = ""
                    for line in rep.longreprtext.splitlines():
                        longerr += line + "\n"
                    self.update_test_error(longerr)

    def append_test_metrics_row(self):

        test_row_text = TestRow(
            sname=str(ConfigVars._suite_name),
            name=str(ConfigVars._test_name),
            stat=str(ConfigVars._test_status),
            dur=str(round(ConfigVars._duration, 2)),
            msg=str(ConfigVars._current_error[:50]),
            runt=str(time.time()).replace('.', '')
        )

        floating_error_text = FloatingError(full_msg=str(ConfigVars._current_error), runt = str(time.time()).replace('.', ''))

        if (self.rerun is not None) and (max_rerun() is not None):
            if (ConfigVars._test_status == 'FAIL') or (ConfigVars._test_status == 'ERROR'): ConfigVars._pvalue += 1

            if (ConfigVars._pvalue == max_rerun() + 1) or (ConfigVars._test_status == 'PASS'):
                if ((ConfigVars._test_status == 'FAIL') or (ConfigVars._test_status == 'ERROR')) and (
                        ConfigVars.screen_base != ''): self.generate_screenshot_data()

                if len(ConfigVars._current_error) < 49:
                    test_row_text.floating_error_text = str('')
                else:
                    test_row_text.floating_error_text = str(floating_error_text)
                    test_row_text.full_msg = str(ConfigVars._current_error)

                ConfigVars._test_metrics_content += str(test_row_text)
                ConfigVars._pvalue = 0

            elif (self.rerun is not None) and (
                    (ConfigVars._test_status == 'xFAIL') or (ConfigVars._test_status == 'xPASS') or (
                    ConfigVars._test_status == 'SKIP')):

                if len(ConfigVars._current_error) < 49:
                    test_row_text.floating_error_text = ''
                else:
                    test_row_text.floating_error_text = str(floating_error_text)
                    test_row_text.full_msg = str(ConfigVars._current_error)

                ConfigVars._test_metrics_content += str(test_row_text)

        elif (self.rerun is None) or (max_rerun() is None):
            if ((ConfigVars._test_status == 'FAIL') or (ConfigVars._test_status == 'ERROR')) and (
                    ConfigVars.screen_base != ''): self.generate_screenshot_data()

            if len(ConfigVars._current_error) < 49:
                test_row_text.floating_error_text = ''
            else:
                test_row_text.floating_error_text = str(floating_error_text)
                test_row_text.full_msg = str(ConfigVars._current_error)

            logging.warning(f"Test Metrics Row: {test_row_text}")

            ConfigVars._test_metrics_content += str(test_row_text)

        self.json_data['content']['suites'].setdefault(len(ConfigVars._test_suite_name), {})['suite_name'] = str(
            ConfigVars._suite_name)
        self.json_data['content']['suites'].setdefault(len(ConfigVars._test_suite_name), {}).setdefault('tests',
                                                                                                        {}).setdefault(
            len(ConfigVars._scenario) - 1, {})['status'] = str(ConfigVars._test_status)
        self.json_data['content']['suites'].setdefault(len(ConfigVars._test_suite_name), {}).setdefault('tests',
                                                                                                        {}).setdefault(
            len(ConfigVars._scenario) - 1, {})['message'] = str(ConfigVars._current_error)
        self.json_data['content']['suites'].setdefault(len(ConfigVars._test_suite_name), {}).setdefault('tests',
                                                                                                        {}).setdefault(
            len(ConfigVars._scenario) - 1, {})['test_name'] = str(ConfigVars._test_name)

        if (self.rerun is not None) and (max_rerun() is not None):
            self.json_data['content']['suites'].setdefault(len(ConfigVars._test_suite_name), {}).setdefault('tests',
                                                                                                            {}).setdefault(
                len(ConfigVars._scenario) - 1, {})['rerun'] = str(self.rerun)
        else:
            self.json_data['content']['suites'].setdefault(len(ConfigVars._test_suite_name), {}).setdefault('tests',
                                                                                                            {}).setdefault(
                len(ConfigVars._scenario) - 1, {})['rerun'] = '0'

    def generate_screenshot_data(self):

        os.makedirs(ConfigVars.screen_base + '/pytest_screenshots', exist_ok=True)

        _screenshot_name = round(time.time())
        _screenshot_suite_name = ConfigVars._suite_name.split('/')[-1:][0].replace('.py', '')
        _screenshot_test_name = ConfigVars._test_name
        if len(ConfigVars._test_name) >= 19: ConfigVars._screenshot_test_name = ConfigVars._test_name[-17:]
        _screenshot_error = ConfigVars._current_error

        ConfigVars.screen_img.save(
            ConfigVars.screen_base + '/pytest_screenshots/' + str(_screenshot_name) + '.png'
        )

        # attach screenshots
        self.attach_screenshots(_screenshot_name, _screenshot_suite_name, _screenshot_test_name, _screenshot_error)
        _screenshot_name = ''
        _screenshot_suite_name = ''
        _screenshot_test_name = ''
        _screenshot_error = ''

    def append_suite_metrics_row(self, name):
        self._test_names(ConfigVars._test_name, clear='yes')
        self._test_suites(name)

        self.json_data['content']['suites'].setdefault(len(ConfigVars._test_suite_name) - 1, {}).setdefault('status',
                                                                                                            {})[
            'total_pass'] = int(ConfigVars._spass_tests)
        self.json_data['content']['suites'].setdefault(len(ConfigVars._test_suite_name) - 1, {}).setdefault('status',
                                                                                                            {})[
            'total_skip'] = int(ConfigVars._sskip_tests)
        self.json_data['content']['suites'].setdefault(len(ConfigVars._test_suite_name) - 1, {}).setdefault('status',
                                                                                                            {})[
            'total_xpass'] = int(ConfigVars._sxpass_tests)
        self.json_data['content']['suites'].setdefault(len(ConfigVars._test_suite_name) - 1, {}).setdefault('status',
                                                                                                            {})[
            'total_xfail'] = int(ConfigVars._sxfail_tests)

        if (self.rerun is not None) and (max_rerun() is not None):
            _base_suite = self.json_data['content']['suites'].setdefault(len(ConfigVars._test_suite_name) - 1, {})[
                'tests']
            for i in _base_suite:
                ConfigVars._srerun_tests += int(_base_suite[int(i)]['rerun'])

            self.json_data['content']['suites'].setdefault(len(ConfigVars._test_suite_name) - 1, {}).setdefault(
                'status', {})[
                'total_rerun'] = int(ConfigVars._srerun_tests)
        else:
            self.json_data['content']['suites'].setdefault(len(ConfigVars._test_suite_name) - 1, {}).setdefault(
                'status', {})[
                'total_rerun'] = 0

        for i in self.json_data['content']['suites'].setdefault(len(ConfigVars._test_suite_name) - 1, {})['tests']:
            if 'ERROR' in \
                    self.json_data['content']['suites'].setdefault(len(ConfigVars._test_suite_name) - 1, {})['tests'][
                        i]['status']:
                ConfigVars._suite_error += 1
            elif 'FAIL' == \
                    self.json_data['content']['suites'].setdefault(len(ConfigVars._test_suite_name) - 1, {})['tests'][
                        i][
                        'status']:
                ConfigVars._suite_fail += 1

        self.json_data['content']['suites'].setdefault(len(ConfigVars._test_suite_name) - 1, {}).setdefault('status',
                                                                                                            {})[
            'total_fail'] = ConfigVars._suite_fail
        self.json_data['content']['suites'].setdefault(len(ConfigVars._test_suite_name) - 1, {}).setdefault('status',
                                                                                                            {})[
            'total_error'] = ConfigVars._suite_error

        suite_row_text = SuiteRow(
            sname=str(name),
            spass=str(ConfigVars._spass_tests),
            sfail=str(ConfigVars._suite_fail),
            sskip=str(ConfigVars._sskip_tests),
            sxpass=str(ConfigVars._sxpass_tests),
            sxfail=str(ConfigVars._sxfail_tests),
            serror=str(ConfigVars._suite_error),
            srerun=str(ConfigVars._srerun_tests)
        )

        ConfigVars._suite_metrics_content += str(suite_row_text)

        self._test_passed(int(ConfigVars._spass_tests))
        self._test_failed(int(ConfigVars._suite_fail))
        self._test_skipped(int(ConfigVars._sskip_tests))
        self._test_xpassed(int(ConfigVars._sxpass_tests))
        self._test_xfailed(int(ConfigVars._sxfail_tests))
        self._test_error(int(ConfigVars._suite_error))

        ConfigVars._spass_tests = 0
        ConfigVars._sfail_tests = 0
        ConfigVars._sskip_tests = 0
        ConfigVars._sxpass_tests = 0
        ConfigVars._sxfail_tests = 0
        ConfigVars._serror_tests = 0
        ConfigVars._srerun_tests = 0
        ConfigVars._suite_fail = 0
        ConfigVars._suite_error = 0

    def set_initial_trigger(self):
        ConfigVars._initial_trigger = False

    def update_previous_suite_name(self):
        ConfigVars._previous_suite_name = ConfigVars._suite_name

    def update_counts(self, rep):
        if rep.when == "call" and rep.passed:
            if hasattr(rep, "wasxfail"):
                ConfigVars._sxpass_tests += 1
            else:
                ConfigVars._spass_tests += 1

        if rep.failed:
            if getattr(rep, "when", None) == "call":
                if hasattr(rep, "wasxfail"):
                    ConfigVars._sxpass_tests += 1
                else:
                    ConfigVars._sfail_tests += 1
            else:
                pass

        if rep.skipped:
            if hasattr(rep, "wasxfail"):
                ConfigVars._sxfail_tests += 1
            else:
                ConfigVars._sskip_tests += 1

    def update_test_error(self, msg):
        ConfigVars._current_error = msg

    def update_test_status(self, status):
        ConfigVars._test_status = status

    def increment_xpass(self):
        ConfigVars._xpass += 1

    def increment_xfail(self):
        ConfigVars._xfail += 1

    def increment_pass(self):
        ConfigVars._pass += 1

    def increment_fail(self):
        ConfigVars._fail += 1

    def increment_skip(self):
        ConfigVars._skip += 1

    def increment_error(self):
        ConfigVars._error += 1
        ConfigVars._serror_tests += 1

    def _date(self):
        return date.today().strftime("%B %d, %Y")

    def _test_suites(self, name):
        ConfigVars._test_suite_name.append(name.split('/')[-1].replace('.py', ''))

    def _test_names(self, name, **kwargs):
        if (self.rerun is None) or (max_rerun() is None): ConfigVars._scenario.append(name)
        try:
            if kwargs['clear'] == 'yes': ConfigVars._scenario = []
        except Exception:
            pass

    def _test_passed(self, value):
        ConfigVars._test_pass_list.append(value)

    def _test_failed(self, value):
        ConfigVars._test_fail_list.append(value)

    def _test_skipped(self, value):
        ConfigVars._test_skip_list.append(value)

    def _test_xpassed(self, value):
        ConfigVars._test_xpass_list.append(value)

    def _test_xfailed(self, value):
        ConfigVars._test_xfail_list.append(value)

    def _test_error(self, value):
        ConfigVars._test_error_list.append(value)

    def renew_template_text(self, logo_url):
        template_text = HtmlTemplate(
            custom_logo=logo_url,
            execution_time=str(ConfigVars._execution_time),
            title=ConfigVars._title,
            env=ConfigVars._env,
            total=str(
                ConfigVars._aspass + ConfigVars._asfail + ConfigVars._asskip + ConfigVars._aserror + ConfigVars._asxpass + ConfigVars._asxfail),
            executed=str(ConfigVars._executed),
            _pass=str(ConfigVars._aspass),
            fail=str(ConfigVars._asfail),
            skip=str(ConfigVars._asskip),
            error=str(ConfigVars._aserror),
            xpass=str(ConfigVars._asxpass),
            xfail=str(ConfigVars._asxfail),
            rerun=str(ConfigVars._asrerun),
            suite_metrics_row=str(ConfigVars._suite_metrics_content),
            test_metrics_row=str(ConfigVars._test_metrics_content),
            date=str(self._date()),
            test_suites=str(ConfigVars._test_suite_name),
            test_suite_length=str(len(ConfigVars._test_suite_name)),
            test_suite_pass=str(ConfigVars._test_pass_list),
            test_suites_fail=str(ConfigVars._test_fail_list),
            test_suites_skip=str(ConfigVars._test_skip_list),
            test_suites_xpass=str(ConfigVars._test_xpass_list),
            test_suites_xfail=str(ConfigVars._test_fail_list),
            test_suites_error=str(ConfigVars._test_error_list),
            archive_status=str(ConfigVars._archive_tab_content),
            archive_body_content=str(ConfigVars._archive_body_content),
            archive_count=str(ConfigVars._archive_count),
            archives=str(ConfigVars.archives),
            max_failure_suite_name_final=str(ConfigVars.max_failure_suite_name_final),
            max_failure_suite_count=str(ConfigVars.max_failure_suite_count),
            similar_max_failure_suite_count=str(ConfigVars.similar_max_failure_suite_count),
            max_failure_total_tests=str(ConfigVars.max_failure_total_tests),
            max_failure_percent=str(ConfigVars.max_failure_percent),
            trends_label=str(ConfigVars.trends_label),
            tpass=str(ConfigVars.tpass),
            tfail=str(ConfigVars.tfail),
            tskip=str(ConfigVars.tskip),
            attach_screenshot_details=str(ConfigVars._attach_screenshot_details)
        )

        # template_text = template_text.replace("__executed_by__", str(platform.uname()[1]))
        # template_text = template_text.replace("__os_name__", str(platform.uname()[0]))
        # template_text = template_text.replace("__python_version__", str(sys.version.split(' ')[0]))
        # template_text = template_text.replace("__generated_date__", str(datetime.datetime.now().strftime("%b %d %Y, %H:%M")))

        return str(template_text)

    def generate_json_data(self, base):
        self.json_data['date'] = self._date()
        self.json_data['start_time'] = ConfigVars._start_execution_time
        self.json_data['total_suite'] = len(ConfigVars._test_suite_name)

        suite = self.json_data['content']['suites']
        for i in suite:
            for k in self.json_data['content']['suites'][i]['status']:
                if (k == 'total_fail' or k == 'total_error') and self.json_data['content']['suites'][i]['status'][k] != 0:
                    self.json_data['status'] = "FAIL"
                    break
                else:
                    continue

            try:
                if self.json_data['status'] == "FAIL": break
            except KeyError:
                if len(ConfigVars._test_suite_name) == i + 1: self.json_data['status'] = "PASS"

        for i in suite:
            for k in self.json_data['content']['suites'][i]['status']:
                if k == 'total_pass':
                    ConfigVars._aspass += self.json_data['content']['suites'][i]['status'][k]
                elif k == 'total_fail':
                    ConfigVars._asfail += self.json_data['content']['suites'][i]['status'][k]
                elif k == 'total_skip':
                    ConfigVars._asskip += self.json_data['content']['suites'][i]['status'][k]
                elif k == 'total_error':
                    ConfigVars._aserror += self.json_data['content']['suites'][i]['status'][k]
                elif k == 'total_xpass':
                    ConfigVars._asxpass += self.json_data['content']['suites'][i]['status'][k]
                elif k == 'total_xfail':
                    ConfigVars._asxfail += self.json_data['content']['suites'][i]['status'][k]
                elif k == 'total_rerun':
                    ConfigVars._asrerun += self.json_data['content']['suites'][i]['status'][k]

        ConfigVars._astotal = ConfigVars._aspass + ConfigVars._asfail + ConfigVars._asskip + ConfigVars._aserror + ConfigVars._asxpass + ConfigVars._asxfail

        self.json_data.setdefault('status_list', {})['pass'] = str(ConfigVars._aspass)
        self.json_data.setdefault('status_list', {})['fail'] = str(ConfigVars._asfail)
        self.json_data.setdefault('status_list', {})['skip'] = str(ConfigVars._asskip)
        self.json_data.setdefault('status_list', {})['error'] = str(ConfigVars._aserror)
        self.json_data.setdefault('status_list', {})['xpass'] = str(ConfigVars._asxpass)
        self.json_data.setdefault('status_list', {})['xfail'] = str(ConfigVars._asxfail)
        self.json_data.setdefault('status_list', {})['rerun'] = str(ConfigVars._asrerun)
        self.json_data['total_tests'] = str(ConfigVars._astotal)

        with open(base + '/output.json', 'w') as outfile:
            json.dump(self.json_data, outfile)

    def update_archives_template(self, base):
        f = glob.glob(base + '/archive/*.json')
        cf = glob.glob(base + '/output.json')
        if len(f) > 0:
            ConfigVars._archive_count = len(f) + 1
            self.load_archive(cf, value='current')

            f.sort(reverse=True)
            self.load_archive(f, value='history')
        else:
            ConfigVars._archive_count = 1
            self.load_archive(cf, value='current')

    def load_archive(self, f, value):
        def state(data):
            if data == 'fail':
                return 'times', '#fc6766'
            elif data == 'pass':
                return 'check', '#98cc64'

        for i, val in enumerate(f):
            with open(val) as json_file:
                data = json.load(json_file)

                suite_highlights(data)
                archive_row_text = ArchiveRow(astate=state(data['status'].lower())[0],
                                              astate_color=state(data['status'].lower())[1])
                if value == "current":
                    archive_row_text.astatus = 'build #' + str(ConfigVars._archive_count)
                    archive_row_textacount = str(ConfigVars._archive_count)
                else:
                    archive_row_text.astatus = 'build #' + str(len(f) - i)
                    archive_row_text.acount = str(len(f) - i)

                adate = datetime.strptime(
                    data['date'].split(None, 1)[0][:1 + 2:] + ' ' +
                    data['date'].split(None, 1)[1].replace(',', ''), "%b %d %Y"
                )

                atime = \
                    "".join(list(filter(lambda x: ':' in x, time.ctime(float(data['start_time'])).split(' ')))).rsplit(
                        ':',
                        1)[0]
                archive_row_text.adate = str(adate.date()) + ' | ' + str(time_converter(atime))
                ConfigVars._archive_tab_content += str(archive_row_text)

                _archive_body_text = ArchiveBody(
                    total_tests=data['total_tests'],
                    date=data['date'].upper(),
                    _pass=data['status_list']['pass'],
                    fail=data['status_list']['fail'],
                    skip=data['status_list']['skip'],
                    xpass=data['status_list']['xpass'],
                    xfail=data['status_list']['xfail'],
                    error=data['status_list']['error'],
                    status=data['status'].lower()
                )

                if value == "current":
                    _archive_body_text.iloop = str(i)
                    _archive_body_text.acount = str(ConfigVars._archive_count)
                else:
                    _archive_body_text.iloop = str(i + 1)
                    _archive_body_text.acount = str(len(f) - i)

                try:
                    _archive_body_text.rerun = data['status_list']['rerun']
                except KeyError:
                    _archive_body_text.rerun = '0'

                index = i
                if value != "current": index = i + 1
                ConfigVars.archives.setdefault(str(index), {})['pass'] = data['status_list']['pass']
                ConfigVars.archives.setdefault(str(index), {})['fail'] = data['status_list']['fail']
                ConfigVars.archives.setdefault(str(index), {})['skip'] = data['status_list']['skip']
                ConfigVars.archives.setdefault(str(index), {})['xpass'] = data['status_list']['xpass']
                ConfigVars.archives.setdefault(str(index), {})['xfail'] = data['status_list']['xfail']
                ConfigVars.archives.setdefault(str(index), {})['error'] = data['status_list']['error']

                try:
                    ConfigVars.archives.setdefault(str(index), {})['rerun'] = data['status_list']['rerun']
                except KeyError:
                    ConfigVars.archives.setdefault(str(index), {})['rerun'] = '0'

                ConfigVars.archives.setdefault(str(index), {})['total'] = data['total_tests']
                ConfigVars._archive_body_content += str(_archive_body_text)

    def update_trends(self, base):

        f2 = glob.glob(base + '/output.json')
        with open(f2[0]) as json_file:
            data = json.load(json_file)
            adate = datetime.strptime(
                data['date'].split(None, 1)[0][:1 + 2:] + ' ' +
                data['date'].split(None, 1)[1].replace(',', ''), "%b %d %Y"
            )
            atime = \
                "".join(list(filter(lambda x: ':' in x, time.ctime(float(data['start_time'])).split(' ')))).rsplit(
                    ':',
                    1)[0]
            ConfigVars.trends_label.append(
                str(time_converter(atime)).upper() + ' | ' + str(adate.date().strftime("%b")) + ' '
                + str(adate.date().strftime("%d")))

            ConfigVars.tpass.append(data['status_list']['pass'])
            ConfigVars.tfail.append(int(data['status_list']['fail']) + int(data['status_list']['error']))
            ConfigVars.tskip.append(data['status_list']['skip'])

        f = glob.glob(base + '/archive' + '/*.json')
        f.sort(reverse=True)

        for i, val in enumerate(f):
            with open(val) as json_file:
                data = json.load(json_file)

                adate = datetime.strptime(
                    data['date'].split(None, 1)[0][:1 + 2:] + ' ' +
                    data['date'].split(None, 1)[1].replace(',', ''), "%b %d %Y"
                )
                atime = \
                    "".join(list(filter(lambda x: ':' in x, time.ctime(float(data['start_time'])).split(' ')))).rsplit(
                        ':',
                        1)[0]
                ConfigVars.trends_label.append(
                    str(time_converter(atime)).upper() + ' | ' + str(adate.date().strftime("%b")) + ' '
                    + str(adate.date().strftime("%d")))

                ConfigVars.tpass.append(data['status_list']['pass'])
                ConfigVars.tfail.append(int(data['status_list']['fail']) + int(data['status_list']['error']))
                ConfigVars.tskip.append(data['status_list']['skip'])

                if i == 4: break

    def attach_screenshots(self, screen_name, test_suite, test_case, test_error):

        _screenshot_details = ScreenshotDetails(
            screen_name=str(screen_name),
            ts=str(test_suite),
            tc=str(test_case),
            te=str(test_error)
        )

        if len(test_case) == 17: test_case = '..' + test_case

        ConfigVars._attach_screenshot_details += str(_screenshot_details)
