import cockpit from "cockpit";
import React from "react";
import PropTypes from "prop-types";
import { log_cmd, get_date_string, get_date_diff, displayKBytes } from "../tools.jsx";
import {
    ConnectionTable,
    DiskTable,
} from "./monitorTables.jsx";
import {
    Button,
    Card,
    CardBody,
    Grid,
    GridItem,
    Tab,
    Tabs,
    TabTitleText,
    Text,
    TextContent,
    TextVariants,
} from '@patternfly/react-core';
import {
    Chart,
    ChartArea,
    ChartAxis,
    ChartGroup,
    ChartThemeColor,
    ChartVoronoiContainer,
} from '@patternfly/react-charts';
import { SyncAltIcon } from "@patternfly/react-icons";

const _ = cockpit.gettext;

export class ServerMonitor extends React.Component {
    constructor (props) {
        super(props);

        const initChart = [];
        for (let idx = 1; idx <= 10; idx++) {
            initChart.push({ name: '', x: idx.toString(), y: 0 });
        }

        this.state = {
            activeKey: 0,
            count: 10,
            port: 389,
            secure_port: 636,
            conn_highmark: 1000,
            cpu_tick_values: [25, 50, 75, 100],
            mem_tick_values: [25, 50, 75, 100],
            conn_tick_values: [250, 500, 750, 1000],
            mem_ratio: 0,
            chart_refresh: "",
            initChart,
            cpuChart: [...initChart],
            memVirtChart: [...initChart],
            memResChart: [...initChart],
            connChart: [...initChart],
        };

        this.handleNavSelect = (event, tabIndex) => {
            this.setState({
                activeKey: tabIndex
            });
        };

        this.startRefresh = this.startRefresh.bind(this);
        this.refreshCharts = this.refreshCharts.bind(this);
        this.stopRefresh = this.stopRefresh.bind(this);
    }

    componentDidMount() {
        this.props.enableTree();
        this.refreshCharts();
        this.startRefresh();
    }

    componentWillUnmount() {
        this.stopRefresh();
    }

    getPorts() {
        const cmd = [
            "dsconf", "-j", "ldapi://%2fvar%2frun%2fslapd-" + this.props.serverId + ".socket",
            "config", "get", "nsslapd-port", "nsslapd-secureport"
        ];
        log_cmd("getPorts", "Get the server port numbers", cmd);
        cockpit
                .spawn(cmd, { superuser: true, err: "message" })
                .done(content => {
                    const config = JSON.parse(content);
                    this.setState({
                        port: config.attrs['nsslapd-port'][0],
                        secure_port:  config.attrs['nsslapd-secureport'] === undefined
                            ? "-1"
                            : config.attrs['nsslapd-secureport'][0],
                    });
                });
    }

    resetChartData() {
        this.setState({
            conn_highmark: 1000,
            cpu_tick_values: [25, 50, 75, 100],
            mem_tick_values: [],
            conn_tick_values: [250, 500, 750, 1000],
            cpuChart: [...this.state.initChart],
            memVirtChart: [...this.state.initChart],
            memResChart: [...this.state.initChart],
            connChart: [...this.state.initChart],
        });
    }

    convertMemory(mem_str) {
        mem_str = mem_str.replace(",", ".");
        if (mem_str.endsWith('m')) {
            // Convert MB to KB
            const mem = mem_str.slice(0, -1);
            return parseInt(Math.round(mem * 1024));
        } else if (mem_str.endsWith('g')) {
            // Convert GB to KB
            const mem = mem_str.slice(0, -1);
            return parseInt(Math.round(mem * 1024 * 1024));
        } else if (mem_str.endsWith('t')) {
            // Convert TB to KB
            const mem = mem_str.slice(0, -1);
            return parseInt(Math.round(mem * 1024 * 1024 * 1024));
        } else if (mem_str.endsWith('p')) {
            // Convert PB to KB
            const mem = mem_str.slice(0, -1);
            return parseInt(Math.round(mem * 1024 * 1024 * 1024 * 1024));
        } else {
            return mem_str;
        }
    }

    refreshCharts() {
        const cmd = "ps -ef | grep -v grep | grep dirsrv/slapd-" + this.props.serverId;
        let cpu = 0;
        let pid = 0;
        let max_mem = 0;
        let virt_mem = 0;
        let res_mem = 0;
        let current_conns = 0;
        let conn_highmark = this.state.conn_highmark;
        let cpu_tick_values = this.state.cpu_tick_values;
        let conn_tick_values = this.state.conn_tick_values;

        // log_cmd("refreshCharts", "Get server pid", [cmd]);
        cockpit
                .script(cmd, [], { superuser: true, err: "message" })
                .done(output => {
                    pid = output.split(/\s+/)[1];
                    const cpu_cmd = "top -n 1 -b -p " + pid + " | tail -1";
                    // log_cmd("refreshCharts", "Get cpu and memory stats", [cpu_cmd]);
                    cockpit
                            .script(cpu_cmd, [], { superuser: true, err: "message" })
                            .done(top_output => {
                                const top_parts = top_output.trim().split(/\s+/);
                                virt_mem = this.convertMemory(top_parts[4]);
                                res_mem = this.convertMemory(top_parts[5]);
                                cpu = parseInt(top_parts[8]);
                                const mem_cmd = "awk '/MemTotal/{print $2}' /proc/meminfo";
                                // log_cmd("refreshCharts", "Get total memory", [mem_cmd]);
                                cockpit
                                        .script(mem_cmd, [], { superuser: true, err: "message" })
                                        .done(mem_output => {
                                            max_mem = parseInt(mem_output);
                                            const conn_cmd = "netstat -anp | grep ':" + this.state.port + "\\|:" + this.state.secure_port +
                                                "' | grep ESTABLISHED | grep ns-slapd | wc -l";
                                            // log_cmd("refreshCharts", "Get current count", [conn_cmd]);
                                            cockpit
                                                    .script(conn_cmd, [], { superuser: true, err: "message" })
                                                    .done(conn_output => {
                                                        current_conns = parseInt(conn_output);
                                                        let count = this.state.count + 1; // This is used by all the charts
                                                        if (count === 100) {
                                                            // Keep progress count in check
                                                            count = 1;
                                                        }

                                                        // Adjust CPU chart ticks if CPU goes above 100%
                                                        if (cpu > 100) {
                                                            let resize = true;
                                                            for (const stat of this.state.cpuChart) {
                                                                if (stat.y > cpu) {
                                                                    // There is already a higher CPU in the data
                                                                    resize = false;
                                                                    break;
                                                                }
                                                            }
                                                            if (resize) {
                                                                let incr = Math.ceil(cpu / 4);
                                                                cpu_tick_values = [incr, incr += incr, incr += incr, cpu];
                                                            }
                                                        } else {
                                                            let okToReset = true;
                                                            for (const stat of this.state.cpuChart) {
                                                                if (stat.y > 100) {
                                                                    okToReset = false;
                                                                    break;
                                                                }
                                                            }
                                                            if (okToReset) {
                                                                cpu_tick_values = [25, 50, 75, 100];
                                                            }
                                                        }

                                                        // Set conn tick values
                                                        if (current_conns > conn_highmark) {
                                                            conn_highmark = Math.ceil(current_conns / 1000) * 1000;
                                                            const conn_incr = Math.ceil(conn_highmark / 4);
                                                            let tick = conn_incr;
                                                            conn_tick_values = [
                                                                tick,
                                                                tick += conn_incr,
                                                                tick += conn_incr,
                                                                tick += conn_incr
                                                            ];
                                                        }

                                                        const cpuChart = this.state.cpuChart;
                                                        cpuChart.shift();
                                                        cpuChart.push({ name: _("CPU"), x: count.toString(), y: parseInt(cpu) });

                                                        const memVirtChart = this.state.memVirtChart;
                                                        memVirtChart.shift();
                                                        memVirtChart.push({ name: _("Virtual Memory"), x: count.toString(), y: parseInt(Math.round((virt_mem / max_mem) * 100)) });

                                                        const memResChart = this.state.memResChart;
                                                        memResChart.shift();
                                                        memResChart.push({ name: _("Resident Memory"), x: count.toString(), y: parseInt(Math.round((res_mem / max_mem) * 100)) });

                                                        const connChart = this.state.connChart;
                                                        connChart.shift();
                                                        connChart.push({ name: _("Connections"), x: count.toString(), y: parseInt(current_conns) });

                                                        this.setState({
                                                            count,
                                                            cpu_tick_values,
                                                            conn_tick_values,
                                                            cpuChart,
                                                            memVirtChart,
                                                            memResChart,
                                                            connChart,
                                                            conn_highmark,
                                                            current_conns,
                                                            mem_virt_size: virt_mem,
                                                            mem_res_size: res_mem,
                                                            mem_ratio: Math.round((virt_mem / max_mem) * 100),
                                                            cpu,
                                                        });
                                                    })
                                                    .fail(() => {
                                                        this.resetChartData();
                                                    });
                                        })
                                        .fail(() => {
                                            this.resetChartData();
                                        });
                            })
                            .fail(() => {
                                this.resetChartData();
                            });
                })
                .fail(() => {
                    this.resetChartData();
                });
    }

    startRefresh() {
        this.setState({
            chart_refresh: setInterval(this.refreshCharts, 3000),
        });
    }

    stopRefresh() {
        clearInterval(this.state.chart_refresh);
    }

    render() {
        const {
            cpu,
            connChart,
            cpuChart,
            current_conns,
            memResChart,
            memVirtChart,
            mem_virt_size,
            mem_res_size,
            mem_ratio,
        } = this.state;

        // Generate start time and uptime
        const startTime = this.props.data.starttime[0];
        const currTime = this.props.data.currenttime[0];
        const startDate = get_date_string(this.props.data.starttime[0]);
        const uptime = get_date_diff(startTime, currTime);
        const conn_tick_values = this.state.conn_tick_values;
        let cpu_tick_values = this.state.cpu_tick_values;
        const mem_tick_values = this.state.mem_tick_values;

        // Adjust chart if CPU goes above 100%
        if (cpu > 100) {
            let incr = Math.ceil(cpu / 4);
            cpu_tick_values = [incr, incr += incr, incr += incr, incr += incr];
        } else {
            let okToReset = true;
            for (const stat of cpuChart) {
                if (stat.y > 100) {
                    okToReset = false;
                    break;
                }
            }
            if (okToReset) {
                cpu_tick_values = [25, 50, 75, 100];
            }
        }

        return (
            <div id="monitor-server-page">
                <Grid>
                    <GridItem span={9}>
                        <TextContent>
                            <Text component={TextVariants.h3}>
                                {_("Server Statistics")}
                                <Button
                                    variant="plain"
                                    aria-label={_("Refresh suffix monitor")}
                                    onClick={this.props.handleReload}
                                >
                                    <SyncAltIcon />
                                </Button>
                            </Text>
                        </TextContent>
                    </GridItem>
                </Grid>
                <Tabs isFilled className="ds-margin-top-lg" activeKey={this.state.activeKey} onSelect={this.handleNavSelect}>
                    <Tab eventKey={0} title={<TabTitleText>{_("Resource Charts")}</TabTitleText>}>
                        <Card className="ds-margin-top-lg" isSelectable>
                            <CardBody>
                                <Grid>
                                    <GridItem span="2" className="ds-center" title={_("Established client connections to the server")}>
                                        <TextContent>
                                            <Text component={TextVariants.h5}>
                                                {_("Connections")}
                                            </Text>
                                        </TextContent>
                                        <TextContent>
                                            <Text component={TextVariants.h2}>
                                                <b>{current_conns}</b>
                                            </Text>
                                        </TextContent>
                                    </GridItem>
                                    <GridItem span="10" style={{ height: '175px', width: '600px' }}>
                                        <Chart
                                            ariaDesc="connection stats"
                                            ariaTitle={_("Live Connection Statistics")}
                                            containerComponent={<ChartVoronoiContainer labels={({ datum }) => `${datum.name}: ${datum.y}`} constrainToVisibleArea />}
                                            height={175}
                                            minDomain={{ y: 0 }}
                                            padding={{
                                                bottom: 30,
                                                left: 50,
                                                top: 10,
                                                right: 15,
                                            }}
                                            width={600}
                                        >
                                            <ChartAxis />
                                            <ChartAxis dependentAxis showGrid tickValues={conn_tick_values} />
                                            <ChartGroup>
                                                <ChartArea
                                                    data={connChart}
                                                />
                                            </ChartGroup>
                                        </Chart>
                                    </GridItem>
                                </Grid>
                            </CardBody>
                        </Card>
                        <Grid className="ds-margin-top-lg" hasGutter>
                            <GridItem span={6}>
                                <Card isSelectable>
                                    <CardBody>
                                        <Grid>
                                            <GridItem className="ds-center" span="4">
                                                <TextContent>
                                                    <Text component={TextVariants.h5}>
                                                        {_("Memory Usage")}
                                                    </Text>
                                                </TextContent>
                                                <TextContent>
                                                    <Text component={TextVariants.h3}>
                                                        <b>{mem_ratio}%</b>
                                                    </Text>
                                                </TextContent>
                                                <TextContent>
                                                    <Text className="ds-margin-top-lg" component={TextVariants.h5}>
                                                        {_("Virtual Size")}
                                                    </Text>
                                                </TextContent>
                                                <TextContent>
                                                    <Text component={TextVariants.h3}>
                                                        <b>{displayKBytes(mem_virt_size)}</b>
                                                    </Text>
                                                </TextContent>
                                                <TextContent>
                                                    <Text className="ds-margin-top-lg" component={TextVariants.h5}>
                                                        {_("Resident Size")}
                                                    </Text>
                                                </TextContent>
                                                <TextContent>
                                                    <Text component={TextVariants.h3}>
                                                        <b>{displayKBytes(mem_res_size)}</b>
                                                    </Text>
                                                </TextContent>
                                            </GridItem>
                                            <GridItem span="8" style={{ height: '225px' }}>
                                                <Chart
                                                    ariaDesc="Server Memory Utilization"
                                                    ariaTitle={_("Live Server Memory Statistics")}
                                                    containerComponent={<ChartVoronoiContainer labels={({ datum }) => `${datum.name}: ${datum.y}`} constrainToVisibleArea />}
                                                    height={225}
                                                    minDomain={{ y: 0 }}
                                                    padding={{
                                                        bottom: 30,
                                                        left: 40,
                                                        top: 10,
                                                        right: 15,
                                                    }}
                                                    themeColor={ChartThemeColor.multiUnordered}
                                                >
                                                    <ChartAxis />
                                                    <ChartAxis dependentAxis showGrid tickValues={mem_tick_values} />
                                                    <ChartGroup>
                                                        <ChartArea
                                                            data={memVirtChart}
                                                            interpolation="monotoneX"
                                                        />
                                                        <ChartArea
                                                            data={memResChart}
                                                            interpolation="monotoneX"
                                                        />
                                                    </ChartGroup>
                                                </Chart>
                                            </GridItem>
                                        </Grid>
                                    </CardBody>
                                </Card>
                            </GridItem>
                            <GridItem span={6}>
                                <Card isSelectable>
                                    <CardBody>
                                        <Grid>
                                            <GridItem span="4" className="ds-center">
                                                <TextContent>
                                                    <Text className="ds-margin-top-xlg" component={TextVariants.h5}>
                                                        {_("CPU Usage")}
                                                    </Text>
                                                </TextContent>
                                                <TextContent>
                                                    <Text component={TextVariants.h3}>
                                                        <b>{cpu}%</b>
                                                    </Text>
                                                </TextContent>
                                            </GridItem>
                                            <GridItem span="8" style={{ height: '225px' }}>
                                                <Chart
                                                    ariaDesc="cpu"
                                                    ariaTitle={_("Server CPU Usage")}
                                                    containerComponent={<ChartVoronoiContainer labels={({ datum }) => `${datum.name}: ${datum.y}`} constrainToVisibleArea />}
                                                    height={225}
                                                    minDomain={{ y: 0 }}
                                                    padding={{
                                                        bottom: 30,
                                                        left: 40,
                                                        top: 10,
                                                        right: 15,
                                                    }}
                                                    themeColor={ChartThemeColor.multiUnordered}
                                                >
                                                    <ChartAxis />
                                                    <ChartAxis dependentAxis showGrid tickValues={cpu_tick_values} />
                                                    <ChartGroup>
                                                        <ChartArea
                                                            data={cpuChart}
                                                            interpolation="monotoneX"
                                                        />
                                                    </ChartGroup>
                                                </Chart>
                                            </GridItem>
                                        </Grid>
                                    </CardBody>
                                </Card>
                            </GridItem>
                        </Grid>
                    </Tab>
                    <Tab eventKey={1} title={<TabTitleText>{_("Server Stats")}</TabTitleText>}>
                        <Grid hasGutter className="ds-margin-top-xlg">
                            <GridItem span={3}>
                                {_("Server Instance")}
                            </GridItem>
                            <GridItem span={9}>
                                <b>{"slapd-" + this.props.serverId}</b>
                            </GridItem>
                            <GridItem span={3}>
                                {_("Version")}
                            </GridItem>
                            <GridItem span={9}>
                                <b>{this.props.data.version}</b>
                            </GridItem>
                            <GridItem span={3}>
                                {_("Server Started")}
                            </GridItem>
                            <GridItem span={9}>
                                <b>{startDate}</b>
                            </GridItem>
                            <GridItem span={3}>
                                {_("Server Uptime")}
                            </GridItem>
                            <GridItem span={9}>
                                <b>{uptime}</b>
                            </GridItem>
                            <hr />
                            <GridItem span={3} title="Active threads includes worker threads, listener threads, tasks, and persistent searches">
                                {_("Active Threads")}
                            </GridItem>
                            <GridItem span={2}>
                                <b>{this.props.data.threads}</b>
                            </GridItem>
                            <GridItem span={3}>
                                {_("Threads Waiting To Read")}
                            </GridItem>
                            <GridItem span={2}>
                                <b>{this.props.data.readwaiters}</b>
                            </GridItem>
                            <GridItem span={3}>
                                {_("Conns At Max Threads")}
                            </GridItem>
                            <GridItem span={2}>
                                <b>{this.props.data.currentconnectionsatmaxthreads}</b>
                            </GridItem>
                            <GridItem span={3}>
                                {_("Conns Exceeded Max Threads")}
                            </GridItem>
                            <GridItem span={2}>
                                <b>{this.props.data.maxthreadsperconnhits}</b>
                            </GridItem>
                            <GridItem span={3}>
                                {_("Total Connections")}
                            </GridItem>
                            <GridItem span={2}>
                                <b>{this.props.data.totalconnections}</b>
                            </GridItem>
                            <GridItem span={3}>
                                {_("Current Connections")}
                            </GridItem>
                            <GridItem span={2}>
                                <b>{this.props.data.currentconnections}</b>
                            </GridItem>
                            <GridItem span={3}>
                                {_("Operations Started")}
                            </GridItem>
                            <GridItem span={2}>
                                <b>{this.props.data.opsinitiated}</b>
                            </GridItem>
                            <GridItem span={3}>
                                {_("Operations Completed")}
                            </GridItem>
                            <GridItem span={2}>
                                <b>{this.props.data.opscompleted}</b>
                            </GridItem>
                            <GridItem span={3}>
                                {_("Entries Returned To Clients")}
                            </GridItem>
                            <GridItem span={2}>
                                <b>{this.props.data.entriessent}</b>
                            </GridItem>
                            <GridItem span={3}>
                                {_("Bytes Sent to Clients")}
                            </GridItem>
                            <GridItem span={2}>
                                <b>{this.props.data.bytessent}</b>
                            </GridItem>
                        </Grid>
                    </Tab>
                    <Tab eventKey={2} title={<TabTitleText>{_("Connection Table")}</TabTitleText>}>
                        <ConnectionTable conns={this.props.data.connection} />
                    </Tab>
                    <Tab eventKey={3} title={<TabTitleText>{_("Disk Space")}</TabTitleText>}>
                        <DiskTable
                            rows={this.props.disks}
                        />
                        <Button
                            className="ds-margin-top"
                            onClick={this.props.handleReloadDisks}
                        >
                            {_("Refresh")}
                        </Button>
                    </Tab>
                    <Tab eventKey={4} title={<TabTitleText>{_("SNMP Counters")}</TabTitleText>}>
                        <Grid className="ds-margin-top-xlg" hasGutter>
                            <GridItem span={4}>
                                {_("Anonymous Binds")}
                            </GridItem>
                            <GridItem span={2}>
                                <b>{this.props.snmpData.anonymousbinds}</b>
                            </GridItem>
                            <GridItem span={4}>
                                {_("Referrals")}
                            </GridItem>
                            <GridItem span={2}>
                                <b>{this.props.snmpData.referrals}</b>
                            </GridItem>
                            <GridItem span={4}>
                                {_("Unauthenticated Binds")}
                            </GridItem>
                            <GridItem span={2}>
                                <b>{this.props.snmpData.unauthbinds}</b>
                            </GridItem>
                            <GridItem span={4}>
                                {_("Returned Referrals")}
                            </GridItem>
                            <GridItem span={2}>
                                <b>{this.props.snmpData.referralsreturned}</b>
                            </GridItem>
                            <GridItem span={4}>
                                {_("Simple Auth Binds")}
                            </GridItem>
                            <GridItem span={2}>
                                <b>{this.props.snmpData.simpleauthbinds}</b>
                            </GridItem>
                            <GridItem span={4}>
                                {_("Bind Security Errors")}
                            </GridItem>
                            <GridItem span={2}>
                                <b>{this.props.snmpData.bindsecurityerrors}</b>
                            </GridItem>
                            <GridItem span={4}>
                                {_("Strong Auth Binds")}
                            </GridItem>
                            <GridItem span={2}>
                                <b>{this.props.snmpData.strongauthbinds}</b>
                            </GridItem>
                            <GridItem span={4}>
                                {_("Security Errors")}
                            </GridItem>
                            <GridItem span={2}>
                                <b>{this.props.snmpData.securityerrors}</b>
                            </GridItem>
                            <GridItem span={4}>
                                {_("Initiated Operations")}
                            </GridItem>
                            <GridItem span={2}>
                                <b>{this.props.snmpData.inops}</b>
                            </GridItem>
                            <GridItem span={4}>
                                {_("Errors")}
                            </GridItem>
                            <GridItem span={2}>
                                <b>{this.props.snmpData.errors}</b>
                            </GridItem>
                            <GridItem span={4}>
                                {_("Compare Operations")}
                            </GridItem>
                            <GridItem span={2}>
                                <b>{this.props.snmpData.compareops}</b>
                            </GridItem>
                            <GridItem span={4}>
                                {_("Current Connections")}
                            </GridItem>
                            <GridItem span={2}>
                                <b>{this.props.snmpData.connections}</b>
                            </GridItem>
                            <GridItem span={4}>
                                {_("Add Operations")}
                            </GridItem>
                            <GridItem span={2}>
                                <b>{this.props.snmpData.addentryops}</b>
                            </GridItem>
                            <GridItem span={4}>
                                {_("Total Connections")}
                            </GridItem>
                            <GridItem span={2}>
                                <b>{this.props.snmpData.connectionseq}</b>
                            </GridItem>
                            <GridItem span={4}>
                                {_("Delete Operations")}
                            </GridItem>
                            <GridItem span={2}>
                                <b>{this.props.snmpData.removeentryops}</b>
                            </GridItem>
                            <GridItem span={4}>
                                {_("Conns in Max Threads")}
                            </GridItem>
                            <GridItem span={2}>
                                <b>{this.props.snmpData.connectionsinmaxthreads}</b>
                            </GridItem>
                            <GridItem span={4}>
                                {_("Modify Operations")}
                            </GridItem>
                            <GridItem span={2}>
                                <b>{this.props.snmpData.modifyentryops}</b>
                            </GridItem>
                            <GridItem span={4}>
                                {_("Conns Exceeded Max Threads")}
                            </GridItem>
                            <GridItem span={2}>
                                <b>{this.props.snmpData.connectionsmaxthreadscount}</b>
                            </GridItem>
                            <GridItem span={4}>
                                {_("ModRDN Operations")}
                            </GridItem>
                            <GridItem span={2}>
                                <b>{this.props.snmpData.modifyrdnops}</b>
                            </GridItem>
                            <GridItem span={4}>
                                {_("Bytes Received")}
                            </GridItem>
                            <GridItem span={2}>
                                <b>{this.props.snmpData.bytesrecv}</b>
                            </GridItem>
                            <GridItem span={4}>
                                {_("Search Operations")}
                            </GridItem>
                            <GridItem span={2}>
                                <b>{this.props.snmpData.searchops}</b>
                            </GridItem>
                            <GridItem span={4}>
                                {_("Bytes Sent")}
                            </GridItem>
                            <GridItem span={2}>
                                <b>{this.props.snmpData.bytessent}</b>
                            </GridItem>
                            <GridItem span={4}>
                                {_("One Level Searches")}
                            </GridItem>
                            <GridItem span={2}>
                                <b>{this.props.snmpData.onelevelsearchops}</b>
                            </GridItem>
                            <GridItem span={4}>
                                {_("Entries Returned")}
                            </GridItem>
                            <GridItem span={2}>
                                <b>{this.props.snmpData.entriesreturned}</b>
                            </GridItem>
                            <GridItem span={4}>
                                {_("Whole Tree Searches")}
                            </GridItem>
                            <GridItem span={2}>
                                <b>{this.props.snmpData.wholesubtreesearchops}</b>
                            </GridItem>
                        </Grid>
                    </Tab>
                </Tabs>
            </div>
        );
    }
}

ServerMonitor.propTypes = {
    serverId: PropTypes.string,
    data: PropTypes.object,
    handleReload: PropTypes.func,
    enableTree: PropTypes.func,
};

ServerMonitor.defaultProps = {
    serverId: "",
    data: {},
};

export default ServerMonitor;
